import type { User } from './user';

export interface Order {
  id: number;
  owner: User;
}
