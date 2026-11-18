export default function AuthFrame({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return <main className={`auth-screen${wide ? " auth-screen-wide" : ""}`}>
    <section className="auth-promo" aria-label="PocketPerks workspace information">
      <div className="auth-brand"><b className="auth-mark">P</b><span>PocketPerks<small>Outreach workspace</small></span></div>
      <div className="auth-promo-copy">
        <span className="auth-promo-pill"><i /> Private team workspace</span>
        <h2>Run PocketPerks outreach from one shared workspace.</h2>
        <p>Check lead lists, prepare and review emails, manage campaign schedules, and keep replies with their records.</p>
        <div className="auth-flow" aria-label="Workflow: check leads, review emails, manage campaigns and replies">
          <span><b>01</b> Check leads</span><span><b>02</b> Review emails</span><span><b>03</b> Manage replies</span>
        </div>
      </div>
      <div className="auth-promo-footer">INVITED TEAM MEMBERS <span>·</span> SHARED WORKSPACE</div>
    </section>
    <div className="auth-panel">{children}</div>
  </main>;
}
