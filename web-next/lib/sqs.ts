import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';

const client = new SQSClient({
  region: process.env.AWS_REGION ?? 'us-east-1',
  endpoint: process.env.AWS_ENDPOINT_URL || undefined,
});

export async function enqueueScan(
  scanId: string,
  userId: string,
  params: Record<string, unknown>,
): Promise<void> {
  const queueUrl = process.env.SQS_QUEUE_URL;
  if (!queueUrl) throw new Error('SQS_QUEUE_URL not configured');
  await client.send(new SendMessageCommand({
    QueueUrl: queueUrl,
    MessageBody: JSON.stringify({ scan_id: scanId, user_id: userId, params }),
  }));
}
