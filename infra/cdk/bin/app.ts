#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AgentOsintStack } from '../lib/agent-osint-stack.js';

const app = new cdk.App();

new AgentOsintStack(app, 'AgentOsintProdStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
  },
});
