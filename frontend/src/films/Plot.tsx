/** The plot summary, behind the spoiler toggle it sits behind on every surface. */
export function Plot({ overview }: { overview: string }) {
  if (!overview) return <p className="muted">No plot summary.</p>;
  return (
    <details className="spoiler">
      <summary>Plot summary</summary>
      <p>{overview}</p>
    </details>
  );
}
