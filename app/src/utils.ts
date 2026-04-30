export function clsx(...args: (string | undefined | null | false)[]): string {
  return args.filter(Boolean).join(" ");
}
