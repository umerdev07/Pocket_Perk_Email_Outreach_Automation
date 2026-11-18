const stages = [
  {
    number: "01",
    title: "Import and check leads",
    description: "Upload a CSV or Excel list. Review which rows are ready, duplicated, invalid or opted out before continuing.",
    icon: "✳",
  },
  {
    number: "02",
    title: "Prepare and review emails",
    description: "Create a business-specific draft for each lead. Messages flagged for attention wait for human review.",
    icon: "✎",
  },
  {
    number: "03",
    title: "Schedule and send",
    description: "Choose eligible leads and sending inboxes. Active campaigns follow their schedule, daily caps and follow-up timing.",
    icon: "↗",
  },
  {
    number: "04",
    title: "Manage replies and opt-outs",
    description: "Track replies in the workspace and record opt-outs so those addresses are excluded from future outreach.",
    icon: "↩",
  },
];

const faqs = [
  {
    question: "How are personalized emails prepared and reviewed?",
    answer: "PocketPerks prepares drafts using your campaign context and lead details. Messages flagged by content checks wait for human review before they can proceed.",
  },
  {
    question: "What kind of leads can I import?",
    answer: "You can upload business lead lists in CSV or Excel format. The import review identifies duplicate, invalid and opted-out rows so you can see what is eligible to continue.",
  },
  {
    question: "Where do replies and opt-outs go?",
    answer: "Replies are collected in Lead Replies, where your team can respond in the existing conversation. Opt-outs are recorded in the suppression list and block that address from future imports and campaigns.",
  },
  {
    question: "When does a campaign send?",
    answer: "A campaign must be active. Eligible messages follow its start and end dates, selected sending days and hours, daily campaign cap and selected inbox limits. The sending provider controls the actual delivery time.",
  },
  {
    question: "Does PocketPerks guarantee inbox placement?",
    answer: "No. PocketPerks records confirmed sends and provides message and opt-out controls, but inbox placement also depends on sender and domain reputation, the sender's email setup, recipient behavior and mailbox filters.",
  },
  {
    question: "Can I create an account myself?",
    answer: "No. Workspace access is by invitation. Ask a PocketPerks administrator to invite your work email; you will set your own password from the invitation link.",
  },
  {
    question: "Do team members have separate lead data?",
    answer: "Authorized team members work in one shared PocketPerks company workspace, so campaign, lead and reply records stay together. Their assigned roles control which actions they can use.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <a className="landing-brand" href="#top" aria-label="PocketPerks home">
          <span className="landing-brand-mark">P</span>
          <span>PocketPerks<small>OUTREACH WORKSPACE</small></span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#workspace-overview">Workspace</a>
          <a href="#faq">FAQ</a>
        </nav>
        <a className="landing-login" href="/login">Log in <span aria-hidden="true">↗</span></a>
      </header>

      <section className="landing-hero" id="top">
        <div className="landing-hero-copy">
          <div className="landing-eyebrow"><span /> POCKETPERKS OUTREACH</div>
          <h1>From business leads to <em>thoughtful outreach.</em></h1>
          <p>Import and check leads, prepare business-specific emails, review messages that need attention, and manage campaigns and replies in one workspace.</p>
          <div className="landing-hero-actions">
            <a className="landing-primary" href="/login">Sign in to your workspace <span aria-hidden="true">→</span></a>
            <a className="landing-text-link" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a>
          </div>
          <div className="landing-trust-line"><span className="landing-check">✓</span> Human review for flagged drafts · Clear campaign controls</div>
        </div>
        <div className="landing-hero-art" aria-label="Campaign process preview">
          <div className="landing-orbit landing-orbit-one" />
          <div className="landing-orbit landing-orbit-two" />
          <div className="landing-art-spark spark-one">✳</div>
          <div className="landing-art-spark spark-two">✦</div>
          <div className="landing-preview-card">
            <div className="landing-preview-top"><span className="landing-preview-dot" /> CAMPAIGN OVERVIEW <span className="landing-preview-menu">···</span></div>
            <div className="landing-preview-title">PocketPerks campaign</div>
            <div className="landing-preview-subtitle">From lead import through reply management</div>
            <div className="landing-preview-flow">
              <div className="preview-flow-row"><span className="preview-flow-icon mint">✳</span><span><b>Lead checks</b><small>Duplicates and opt-outs identified</small></span><i>01</i></div>
              <div className="preview-flow-row"><span className="preview-flow-icon lilac">✎</span><span><b>Email review</b><small>Flagged drafts wait for a person</small></span><i>02</i></div>
              <div className="preview-flow-row"><span className="preview-flow-icon peach">↩</span><span><b>Replies and opt-outs</b><small>Follow the conversation in one place</small></span><i>03</i></div>
            </div>
            <div className="landing-preview-footer"><span><b /> Your process, at a glance</span><span>01 — 04</span></div>
          </div>
          <div className="landing-float-note"><span>✦</span><div><b>Campaign-specific context</b><small>Drafts use your instructions and lead details</small></div></div>
        </div>
        <div className="landing-hero-bottom"><span>CAMPAIGN</span><i /> <span>TAILORED EMAILS</span><i /> <span>REPLIES</span><span className="landing-hero-caption">A considered way to introduce PocketPerks</span></div>
      </section>

      <section className="landing-intro" aria-label="Product summary">
        <div><span className="landing-section-kicker">ONE CONNECTED WORKSPACE</span><h2>From a good first draft to a clear next step.</h2></div>
        <p>PocketPerks brings CSV and Excel lead imports, campaign planning, personalized email preparation, content review, scheduled sending and reply management into one straightforward process.</p>
      </section>

      <section className="landing-stages" id="how-it-works">
        <div className="landing-section-heading"><div><span className="landing-section-kicker">HOW IT WORKS</span><h2>Four practical steps.<br /><em>One shared workspace.</em></h2></div><p>Check leads, prepare and review email, manage scheduled sending, then handle replies and opt-outs.</p></div>
        <div className="landing-stage-grid">
          {stages.map((stage) => <article className="landing-stage-card" key={stage.number}>
            <div className="landing-stage-top"><span>{stage.number}</span><i>{stage.icon}</i></div>
            <h3>{stage.title}</h3>
            <p>{stage.description}</p>
            <div className="landing-stage-rule"><span style={{ width: `${Number(stage.number) * 25}%` }} /></div>
          </article>)}
        </div>
      </section>

      <section className="landing-demo" id="workspace-overview">
        <div className="landing-demo-copy"><span className="landing-section-kicker">WHAT THE WORKSPACE INCLUDES</span><h2>Clear controls at <em>each step.</em></h2><p>Keep lead checks, email review, campaign settings and reply handling connected in one place.</p><a className="landing-demo-login" href="/login">Sign in to explore <span aria-hidden="true">→</span></a></div>
        <div className="landing-video-placeholder landing-overview-card" aria-label="PocketPerks workspace features">
          <article><span>01 · LEADS</span><b>Import and check your list</b><small>See imported, duplicate, invalid and opted-out rows.</small></article>
          <article><span>02 · CAMPAIGNS</span><b>Set schedule and sending limits</b><small>Choose sending hours, days, inboxes and campaign caps.</small></article>
          <article><span>03 · EMAILS</span><b>Review drafts and handle replies</b><small>Check flagged messages and keep replies with their lead records.</small></article>
        </div>
      </section>

      <section className="landing-faq" id="faq">
        <div className="landing-faq-heading"><span className="landing-section-kicker">GOOD TO KNOW</span><h2>How PocketPerks <em>works.</em></h2><p>Answers about access, sending and the shared workspace.</p></div>
        <div className="landing-faq-list">{faqs.map((faq) => <details key={faq.question}><summary>{faq.question}<span>+</span></summary><p>{faq.answer}</p></details>)}</div>
      </section>

      <section className="landing-cta"><div><span className="landing-section-kicker">INVITED TEAM MEMBERS</span><h2>Your workspace is ready.</h2><p>Sign in with the email address invited by your PocketPerks administrator.</p></div><a className="landing-primary" href="/login">Go to sign in <span aria-hidden="true">→</span></a><div className="landing-cta-orb" /></section>

      <footer className="landing-footer">
        <a className="landing-brand" href="#top"><span className="landing-brand-mark">P</span><span>PocketPerks<small>OUTREACH WORKSPACE</small></span></a>
        <p>Lead preparation, campaign control and replies in one place.</p>
        <nav aria-label="Footer navigation"><a href="#how-it-works">How it works</a><a href="#workspace-overview">Workspace</a><a href="#faq">FAQ</a><a href="/login">Log in</a></nav>
        <small className="landing-copyright">© {new Date().getFullYear()} PocketPerks · VenturesSky Ltd</small>
      </footer>
    </main>
  );
}
