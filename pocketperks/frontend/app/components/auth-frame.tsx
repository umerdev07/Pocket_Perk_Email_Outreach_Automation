export default function AuthFrame({ children }: { children: React.ReactNode }) {
  return <main className="auth-screen">
    <section className="auth-promo" aria-label="PocketPerks workspace information">
      <div className="auth-brand"><b className="auth-mark">P</b><span>PocketPerks<small>Outreach workspace</small></span></div>
      <div className="auth-promo-copy">
        <span className="auth-promo-pill"><i /> Private team workspace</span>
        <h2>Make every local introduction feel personal.</h2>
        <p>One calm workspace for preparing, reviewing and managing PocketPerks outreach.</p>
        <div className="auth-flow" aria-label="Workflow: prepare, review, manage">
          <span><b>01</b> Prepare</span><span><b>02</b> Review</span><span><b>03</b> Manage</span>
        </div>
      </div>
      <div className="auth-promo-footer">PRIVATE BY INVITATION <span>·</span> POCKETPERKS UK</div>
    </section>
    <div className="auth-panel">{children}</div>
  </main>;
}
