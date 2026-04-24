import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Resolve a public asset path against Vite's base URL. */
export function asset(assetPath: string): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const base = (import.meta as any).env?.BASE_URL ?? "/";
  const joined = `${base}${assetPath}`;
  // Collapse repeated slashes, but preserve the protocol "://".
  return joined.replace(/(^https?:\/\/)|(\/+)/g, (_, proto: string | undefined) =>
    proto ?? "/",
  );
}
