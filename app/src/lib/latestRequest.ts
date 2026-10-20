/** Prevent responses from an older refresh or workspace from replacing current data. */
export class LatestRequest {
  private epoch = 0;
  private versions = new Map<string, number>();
  start(key: string): () => boolean {
    const epoch = this.epoch;
    const version = (this.versions.get(key) ?? 0) + 1;
    this.versions.set(key, version);
    return () => epoch === this.epoch && this.versions.get(key) === version;
  }
  reset(): void { this.epoch++; this.versions.clear(); }
}
