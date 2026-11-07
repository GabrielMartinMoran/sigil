/**
 * Types for `source-hash.mjs`: the script is plain ESM so Node can run it without a
 * build step, and this declaration is what lets `vite.config.ts` import it under
 * `strict`. Keep the two in step — the signature is the contract.
 */

export const INPUT_DIRECTORIES: string[];
export const INPUT_FILES: string[];
export const IGNORED_DIRECTORIES: Set<string>;
export const HASH_CHARS: number;
export const webRoot: string;

export interface InputFile {
  path: string;
  content: Buffer;
}

export function inputFiles(root?: string): Promise<InputFile[]>;
export function sourceHash(root?: string): Promise<string>;
export function builtHash(root?: string): Promise<string | null>;
export function isFresh(root?: string): Promise<boolean>;
