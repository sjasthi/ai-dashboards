
/**
 * An eyebrow-labelled band of the page, with optional controls on the right.
 *
 * `subtitle` stacks under the title within the same header row, so `actions`
 * (e.g. a toggle button) vertically centers against title+subtitle together
 * rather than just the title line.
 */
export default function Section({ title, chip, subtitle, actions, children }) {
  return (
    <section className="section">
      <div className="section__head">
        <div className="section__title-group">
          <div className="section__title">
            <span className="eyebrow">{title}</span>
            {chip}
          </div>
          {subtitle}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
