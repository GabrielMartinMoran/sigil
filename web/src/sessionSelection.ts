/** Match both identity and generation: a selection reply can change only the ID. */
export function isCurrentSession(
  current: { selectedSessionId: string | null; selectionGeneration: number },
  sessionId: string,
  generation: number,
): boolean {
  return current.selectedSessionId === sessionId && current.selectionGeneration === generation;
}
