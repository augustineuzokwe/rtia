export default function App() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="container mx-auto max-w-3xl py-16">
        <h1 className="text-4xl font-semibold tracking-tight">RTIA</h1>
        <p className="mt-2 text-muted-foreground">
          Requirements → backlog-ready user story.
        </p>
        <div className="mt-8 rounded-lg border border-border bg-card p-6 text-card-foreground">
          <p className="text-sm">
            React scaffold (US-16). Phase panels land in US-17 onward. The
            existing Gradio UI remains available at{" "}
            <a className="underline" href="/legacy">
              /legacy
            </a>{" "}
            during the migration.
          </p>
        </div>
      </div>
    </main>
  );
}
