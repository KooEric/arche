export async function loadPlugin(name: string) {
  return import(`./plugins/${name}`);
}

export async function loadFixed() {
  return import('./plugins/alpha');
}

export function loadLegacy(id: string) {
  return require('./plugins/' + id);
}

const lazy = () => import("./plugins/beta.js");

// import('./plugins/commented')
const text = "import('./plugins/in-string')";
const pattern = /import\(['"]\.\/plugins\/in-regex['"]\)/;

export const tools = { lazy, text, pattern };
