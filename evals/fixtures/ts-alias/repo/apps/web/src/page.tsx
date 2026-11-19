import { fmt } from '~/fmt';
import { shared } from '@acme/shared';

export const Page = () => <p>{fmt(shared)}</p>;
