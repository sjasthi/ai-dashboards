
/** An eyebrow-labelled band of the page, with optional controls on the right. */
export default function Section({ title, chip, actions, children }) {
  return (
    <section className="section">
      <div className="section__head">
        <div className="section__title">
          <span className="eyebrow">{title}</span>
          {chip}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
