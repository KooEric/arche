export default class Logger {
  write(tag: string, message: string) {
    log(`${tag}: ${message}`);
  }
}

export const level = 'info';

export function log(line: string) {
  process.stdout.write(line);
}
