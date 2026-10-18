import { z } from "zod";

import en from "../../locales/en.json";

// UI strings live in locales/<lang>.json, shared with the pipeline's renderer.
// Placeholders are `{name}`. `html()` escapes every value for Telegram HTML
// unless it is wrapped in `safe()`; `plain()` is for button labels and popups,
// which Telegram shows as plain text.

interface Tree {
  [key: string]: string | string[] | Tree;
}

const TreeSchema: z.ZodType<Tree> = z.lazy(() =>
  z.record(z.string(), z.union([z.string(), z.array(z.string()), TreeSchema])),
);

export class Safe {
  constructor(readonly value: string) {}
}

export function safe(value: string): Safe {
  return new Safe(value);
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

type Value = string | number | Safe;

export class Locale {
  private readonly tree: Tree;

  constructor(tree: unknown) {
    this.tree = TreeSchema.parse(tree);
  }

  /** The raw string at `key` ("bot.steps.tracks"); throws when it is missing. */
  text(key: string): string {
    const node = this.lookup(key);
    if (typeof node !== "string") {
      throw new Error(`locale has no string at ${key}`);
    }
    return node;
  }

  /** The list at `key` ("months"); throws when it is missing. */
  list(key: string): string[] {
    const node = this.lookup(key);
    if (!Array.isArray(node)) {
      throw new Error(`locale has no list at ${key}`);
    }
    return node;
  }

  html(key: string, values: Record<string, Value> = {}): string {
    return this.fill(key, values, (v) => (v instanceof Safe ? v.value : escapeHtml(String(v))));
  }

  plain(key: string, values: Record<string, Value> = {}): string {
    return this.fill(key, values, (v) => (v instanceof Safe ? v.value : String(v)));
  }

  private lookup(key: string): string | string[] | Tree | undefined {
    let node: string | string[] | Tree | undefined = this.tree;
    for (const part of key.split(".")) {
      node = typeof node === "object" && !Array.isArray(node) ? node[part] : undefined;
    }
    return node;
  }

  private fill(key: string, values: Record<string, Value>, render: (v: Value) => string): string {
    return this.text(key).replace(/\{(\w+)\}/g, (_match, name: string) => {
      const value = values[name];
      if (value === undefined) {
        throw new Error(`locale string ${key} needs {${name}}`);
      }
      return render(value);
    });
  }
}

export const EN = new Locale(en);
