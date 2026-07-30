/**
 * The surface every panel on the dashboard sits on.
 *
 * Exists because the same white-card recipe was hand-repeated in nine places, which
 * is how the page ended up with three slightly different border greys.
 */
export default function Card({ className = '', children, ...rest }) {
  return (
    <div className={`card ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}
