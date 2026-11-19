import { createApp } from './app';
import './setup';

export * from './models';
export { default as Logger } from './lib/logger';

createApp();
