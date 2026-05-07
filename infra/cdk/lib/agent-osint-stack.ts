import * as cdk from 'aws-cdk-lib';
import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as appscaling from 'aws-cdk-lib/aws-applicationautoscaling';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';

export class AgentOsintStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const projectName = this.node.tryGetContext('projectName') ?? 'agent-osint';
    const environmentName = this.node.tryGetContext('environmentName') ?? 'prod';
    const prefix = `${projectName}-${environmentName}`;

    const webTag = this.node.tryGetContext('webImageTag') ?? 'latest';
    const apiTag = this.node.tryGetContext('apiImageTag') ?? 'latest';
    const workerTag = this.node.tryGetContext('workerImageTag') ?? 'latest';
    const webSearchProvider = this.node.tryGetContext('webSearchProvider') ?? 'apify';
    const webExtractProvider = this.node.tryGetContext('webExtractProvider') ?? 'apify';

    const webRepo = this.repository(`${prefix}-web-next`);
    const apiRepo = this.repository(`${prefix}-api-py`);
    const workerRepo = this.repository(`${prefix}-worker-py`);

    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
        { name: 'isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });

    const albSg = new ec2.SecurityGroup(this, 'AlbSecurityGroup', {
      vpc,
      description: 'Internet to ALB',
      allowAllOutbound: true,
    });
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'HTTP from internet');

    const taskSg = new ec2.SecurityGroup(this, 'TaskSecurityGroup', {
      vpc,
      description: 'ALB to ECS tasks',
      allowAllOutbound: true,
    });
    taskSg.addIngressRule(albSg, ec2.Port.tcp(3000), 'ALB to web-next');
    taskSg.addIngressRule(albSg, ec2.Port.tcp(8000), 'ALB to api-py');

    const dataSg = new ec2.SecurityGroup(this, 'DataSecurityGroup', {
      vpc,
      description: 'ECS tasks to RDS and Redis',
      allowAllOutbound: true,
    });
    dataSg.addIngressRule(taskSg, ec2.Port.tcp(5432), 'Tasks to Postgres');
    dataSg.addIngressRule(taskSg, ec2.Port.tcp(6379), 'Tasks to Redis');

    const appSecret = new secretsmanager.Secret(this, 'AppSecret', {
      secretName: `${projectName}/${environmentName}/secrets`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          OPENAI_API_KEY: '',
          XAI_API_KEY: '',
          APIFY_TOKEN: '',
          TAVILY_API_KEY: '',
        }),
        generateStringKey: 'NEXTAUTH_SECRET',
        excludePunctuation: true,
      },
    });

    const dbSecret = new rds.DatabaseSecret(this, 'DatabaseSecret', {
      username: 'app',
      secretName: `${projectName}/${environmentName}/database`,
    });

    const db = new rds.DatabaseInstance(this, 'Postgres', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dataSg],
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16_4,
      }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      allocatedStorage: 20,
      storageType: rds.StorageType.GP3,
      credentials: rds.Credentials.fromSecret(dbSecret),
      databaseName: 'agent_osint',
      backupRetention: Duration.days(7),
      multiAz: false,
      publiclyAccessible: false,
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const redisSubnetGroup = new elasticache.CfnSubnetGroup(this, 'RedisSubnetGroup', {
      description: `${prefix} redis isolated subnets`,
      subnetIds: vpc.isolatedSubnets.map((subnet) => subnet.subnetId),
    });
    const redis = new elasticache.CfnCacheCluster(this, 'Redis', {
      cacheNodeType: 'cache.t4g.micro',
      engine: 'redis',
      numCacheNodes: 1,
      vpcSecurityGroupIds: [dataSg.securityGroupId],
      cacheSubnetGroupName: redisSubnetGroup.ref,
    });
    redis.addDependency(redisSubnetGroup);

    const resultsBucket = new s3.Bucket(this, 'ResultsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      lifecycleRules: [
        {
          transitions: [{ storageClass: s3.StorageClass.INFREQUENT_ACCESS, transitionAfter: Duration.days(90) }],
          expiration: Duration.days(730),
          noncurrentVersionExpiration: Duration.days(30),
        },
      ],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const dlq = new sqs.Queue(this, 'ScansDlq', {
      queueName: `${prefix}-scans-dlq`,
      retentionPeriod: Duration.days(14),
    });
    const queue = new sqs.Queue(this, 'ScansQueue', {
      queueName: `${prefix}-scans`,
      visibilityTimeout: Duration.minutes(90),
      deadLetterQueue: {
        queue: dlq,
        maxReceiveCount: 1,
      },
    });

    const cluster = new ecs.Cluster(this, 'Cluster', {
      vpc,
      clusterName: prefix,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });

    const alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
      loadBalancerName: `${prefix}-alb`,
      idleTimeout: Duration.seconds(300),
    });

    const listener = alb.addListener('HttpListener', {
      port: 80,
      open: true,
    });

    const commonEnv = {
      AWS_REGION: Stack.of(this).region,
      S3_BUCKET: resultsBucket.bucketName,
      SQS_QUEUE_URL: queue.queueUrl,
      REDIS_URL: `redis://${redis.attrRedisEndpointAddress}:${redis.attrRedisEndpointPort}/0`,
      LOG_LEVEL: 'INFO',
      OSINT_WEB_SEARCH_PROVIDER: webSearchProvider,
      OSINT_WEB_EXTRACT_PROVIDER: webExtractProvider,
      DATABASE_HOST: db.dbInstanceEndpointAddress,
      DATABASE_PORT: db.dbInstanceEndpointPort,
      DATABASE_NAME: 'agent_osint',
    };

    const commonSecrets = {
      DATABASE_USER: ecs.Secret.fromSecretsManager(dbSecret, 'username'),
      DATABASE_PASSWORD: ecs.Secret.fromSecretsManager(dbSecret, 'password'),
      NEXTAUTH_SECRET: ecs.Secret.fromSecretsManager(appSecret, 'NEXTAUTH_SECRET'),
    };

    const webTask = new ecs.FargateTaskDefinition(this, 'WebTask', {
      family: `${prefix}-web-next`,
      cpu: 512,
      memoryLimitMiB: 1024,
    });
    const webContainer = webTask.addContainer('web-next', {
      image: ecs.ContainerImage.fromEcrRepository(webRepo, webTag),
      logging: ecs.LogDrivers.awsLogs(this.logOptions('/ecs/agent-osint/web-next')),
      environment: {
        ...commonEnv,
        NEXTAUTH_URL: `http://${alb.loadBalancerDnsName}`,
        API_BASE_INTERNAL: `http://${alb.loadBalancerDnsName}`,
        NEXT_PUBLIC_API_BASE: '',
        MAX_CONCURRENT_SCANS_PER_USER: '2',
      },
      secrets: commonSecrets,
      command: [
        'sh',
        '-c',
        'export DATABASE_URL_NODE="postgresql://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}?sslmode=require"; exec node server.js',
      ],
    });
    webContainer.addPortMappings({ containerPort: 3000 });

    const apiTask = new ecs.FargateTaskDefinition(this, 'ApiTask', {
      family: `${prefix}-api-py`,
      cpu: 512,
      memoryLimitMiB: 1024,
    });
    const apiContainer = apiTask.addContainer('api-py', {
      image: ecs.ContainerImage.fromEcrRepository(apiRepo, apiTag),
      logging: ecs.LogDrivers.awsLogs(this.logOptions('/ecs/agent-osint/api-py')),
      environment: commonEnv,
      secrets: commonSecrets,
      command: [
        'sh',
        '-c',
        'export DATABASE_URL="postgresql+psycopg://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}?sslmode=require"; exec uvicorn osint.api.app:app --host 0.0.0.0 --port 8000',
      ],
    });
    apiContainer.addPortMappings({ containerPort: 8000 });

    const workerTask = new ecs.FargateTaskDefinition(this, 'WorkerTask', {
      family: `${prefix}-worker-py`,
      cpu: 1024,
      memoryLimitMiB: 2048,
    });
    workerTask.addContainer('worker-py', {
      image: ecs.ContainerImage.fromEcrRepository(workerRepo, workerTag),
      logging: ecs.LogDrivers.awsLogs(this.logOptions('/ecs/agent-osint/worker-py')),
      environment: {
        ...commonEnv,
        SCAN_VISIBILITY_TIMEOUT_SECONDS: '5400',
        SCAN_HEARTBEAT_SECONDS: '300',
      },
      secrets: {
        ...commonSecrets,
        OPENAI_API_KEY: ecs.Secret.fromSecretsManager(appSecret, 'OPENAI_API_KEY'),
        XAI_API_KEY: ecs.Secret.fromSecretsManager(appSecret, 'XAI_API_KEY'),
        APIFY_TOKEN: ecs.Secret.fromSecretsManager(appSecret, 'APIFY_TOKEN'),
        TAVILY_API_KEY: ecs.Secret.fromSecretsManager(appSecret, 'TAVILY_API_KEY'),
      },
      command: [
        'sh',
        '-c',
        'export DATABASE_URL="postgresql+psycopg://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}?sslmode=require"; exec python -m osint.worker.main',
      ],
    });

    for (const task of [webTask, apiTask, workerTask]) {
      appSecret.grantRead(task.executionRole!);
      dbSecret.grantRead(task.executionRole!);
      task.addToExecutionRolePolicy(new iam.PolicyStatement({
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'],
      }));
    }

    queue.grantSendMessages(webTask.taskRole);
    resultsBucket.grantRead(apiTask.taskRole, 'scans/*');
    queue.grantConsumeMessages(workerTask.taskRole);
    resultsBucket.grantReadWrite(workerTask.taskRole, 'scans/*');

    const webService = new ecs.FargateService(this, 'WebService', {
      cluster,
      serviceName: `${prefix}-web-next`,
      taskDefinition: webTask,
      desiredCount: 1,
      minHealthyPercent: 100,
      securityGroups: [taskSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const apiService = new ecs.FargateService(this, 'ApiService', {
      cluster,
      serviceName: `${prefix}-api-py`,
      taskDefinition: apiTask,
      desiredCount: 1,
      minHealthyPercent: 100,
      securityGroups: [taskSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const workerService = new ecs.FargateService(this, 'WorkerService', {
      cluster,
      serviceName: `${prefix}-worker-py`,
      taskDefinition: workerTask,
      desiredCount: 1,
      minHealthyPercent: 100,
      securityGroups: [taskSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const webTarget = listener.addTargets('WebTarget', {
      port: 3000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [webService],
      healthCheck: {
        path: '/auth/signin',
        healthyHttpCodes: '200,302',
      },
    });
    const apiTarget = new elbv2.ApplicationTargetGroup(this, 'ApiTarget', {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [apiService],
      healthCheck: {
        path: '/healthz',
        healthyHttpCodes: '200',
      },
    });
    listener.addTargetGroups('ApiPathRule', {
      priority: 10,
      conditions: [elbv2.ListenerCondition.pathPatterns(['/api/*', '/healthz'])],
      targetGroups: [apiTarget],
    });

    const workerScaling = workerService.autoScaleTaskCount({
      minCapacity: 1,
      maxCapacity: 2,
    });
    workerScaling.scaleOnMetric('QueueDepthScaling', {
      metric: queue.metricApproximateNumberOfMessagesVisible(),
      scalingSteps: [
        { upper: 0, change: -1 },
        { lower: 1, change: +1 },
      ],
      adjustmentType: appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
      cooldown: Duration.minutes(2),
    });

    new budgets.CfnBudget(this, 'MonthlyBudget', {
      budget: {
        budgetName: `${prefix}-monthly-budget`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: 200,
          unit: 'USD',
        },
      },
    });

    new cdk.CfnOutput(this, 'AlbUrl', {
      value: `http://${alb.loadBalancerDnsName}`,
    });
    new cdk.CfnOutput(this, 'WebRepositoryUri', { value: webRepo.repositoryUri });
    new cdk.CfnOutput(this, 'ApiRepositoryUri', { value: apiRepo.repositoryUri });
    new cdk.CfnOutput(this, 'WorkerRepositoryUri', { value: workerRepo.repositoryUri });
    new cdk.CfnOutput(this, 'ScansQueueUrl', { value: queue.queueUrl });
    new cdk.CfnOutput(this, 'ResultsBucketName', { value: resultsBucket.bucketName });
    new cdk.CfnOutput(this, 'TaskSecurityGroupId', { value: taskSg.securityGroupId });
    new cdk.CfnOutput(this, 'PrivateSubnetIds', {
      value: vpc.privateSubnets.map((subnet) => subnet.subnetId).join(','),
    });

    webTarget.setAttribute('deregistration_delay.timeout_seconds', '30');
    apiTarget.setAttribute('deregistration_delay.timeout_seconds', '30');
  }

  private repository(repositoryName: string): ecr.IRepository {
    return ecr.Repository.fromRepositoryName(this, `${repositoryName}Repo`, repositoryName);
  }

  private logOptions(streamPrefix: string): ecs.AwsLogDriverProps {
    return {
      streamPrefix,
      logRetention: logs.RetentionDays.ONE_MONTH,
    };
  }
}
