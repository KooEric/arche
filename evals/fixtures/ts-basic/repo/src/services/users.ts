import { User } from '../models';
import { PORT } from '../config';

export type Query = { id: string };

const cache = new Map<string, User>();

export async function findUser(query: Query): Promise<User | undefined> {
  return cache.get(query.id + PORT);
}
