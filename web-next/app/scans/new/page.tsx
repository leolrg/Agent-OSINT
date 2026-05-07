import { fetchAgentCatalog } from '../../../lib/api';
import { NewScanForm } from '../../../components/NewScanForm';

export default async function NewScanPage() {
  const catalog = await fetchAgentCatalog();
  return <NewScanForm catalog={catalog} />;
}
