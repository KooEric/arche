import express from 'express';
import type { User } from './models/user';
import { findUser, type Query } from './services/users.js';
import * as config from './config';
import Logger, { level } from './lib/logger';

export function createApp() {
  const app = express();
  const logger = new Logger();
  app.get('/users/:id', async (req, res) => {
    const query: Query = { id: req.params.id };
    const user: User | undefined = await findUser(query);
    logger.write(level, String(config.PORT));
    res.json(user);
  });
  return app;
}
