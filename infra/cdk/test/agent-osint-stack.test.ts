import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentOsintStack } from '../lib/agent-osint-stack.js';

const app = new cdk.App({
  context: {
    projectName: 'agent-osint',
    environmentName: 'prod',
    webImageTag: 'test-sha',
    apiImageTag: 'test-sha',
    workerImageTag: 'test-sha',
  },
});
const stack = new AgentOsintStack(app, 'TestStack', {
  env: { account: '123456789012', region: 'us-east-1' },
});
const template = Template.fromStack(stack);

template.resourceCountIs('AWS::ECS::Service', 3);
template.resourceCountIs('AWS::ECS::Cluster', 1);
template.resourceCountIs('AWS::ElasticLoadBalancingV2::LoadBalancer', 1);
template.resourceCountIs('AWS::RDS::DBInstance', 1);
template.resourceCountIs('AWS::ElastiCache::CacheCluster', 1);
template.resourceCountIs('AWS::SecretsManager::Secret', 2);
template.resourceCountIs('AWS::S3::Bucket', 1);

template.hasResourceProperties('AWS::ElasticLoadBalancingV2::ListenerRule', {
  Conditions: Match.arrayWith([
    Match.objectLike({
      Field: 'path-pattern',
      PathPatternConfig: {
        Values: Match.arrayWith(['/api/*']),
      },
    }),
  ]),
});

template.hasResourceProperties('AWS::ApplicationAutoScaling::ScalableTarget', {
  MaxCapacity: 2,
  MinCapacity: 1,
  ServiceNamespace: 'ecs',
});

template.hasResourceProperties('AWS::SQS::Queue', {
  VisibilityTimeout: 5400,
});

template.hasResourceProperties('AWS::ECS::TaskDefinition', {
  ContainerDefinitions: Match.arrayWith([
    Match.objectLike({
      Name: 'web-next',
      PortMappings: Match.arrayWith([Match.objectLike({ ContainerPort: 3000 })]),
      Environment: Match.arrayWith([
        Match.objectLike({ Name: 'MAX_CONCURRENT_SCANS_PER_USER', Value: '2' }),
      ]),
    }),
  ]),
});
