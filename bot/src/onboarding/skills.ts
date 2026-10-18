import type { SharedConfig } from "../config";

export interface ParsedSkills {
  ids: string[]; // skills.yaml ids
  unknown: string[]; // lower-cased as typed; the scorer compares them as is
}

export function skillIndex(skills: SharedConfig["skills"]): Map<string, string> {
  const index = new Map<string, string>();
  for (const skill of skills) {
    for (const name of [skill.id, skill.name, ...skill.aliases]) {
      index.set(name.toLowerCase(), skill.id);
    }
  }
  return index;
}

/** "Python, sql; React.js" → skills.yaml ids, keeping anything unknown as typed. */
export function parseSkills(
  text: string,
  index: Map<string, string>,
  limits: { max_skills: number; max_skill_length: number },
): ParsedSkills {
  const ids: string[] = [];
  const unknown: string[] = [];
  for (const piece of text.split(/[,;\n]/)) {
    const name = piece.trim().replace(/\s+/g, " ").toLowerCase();
    if (!name || name.length > limits.max_skill_length) {
      continue;
    }
    const id = index.get(name);
    if (id !== undefined) {
      if (!ids.includes(id)) ids.push(id);
    } else if (!unknown.includes(name)) {
      unknown.push(name);
    }
    if (ids.length + unknown.length >= limits.max_skills) {
      break;
    }
  }
  return { ids, unknown };
}
