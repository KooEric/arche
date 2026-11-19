import 'dotenv/config';
import { readFileSync } from 'node:fs';

const data = require('./data.json');

export const banner = readFileSync('banner.txt', 'utf8') + data.seed;
