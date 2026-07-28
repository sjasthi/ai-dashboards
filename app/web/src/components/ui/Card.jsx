import React from 'react';

/**
 * The surface every panel on the dashboard sits on.
 *
 * Exists because the same white-card recipe was hand-repeated in nine places, which
 * is how the page ended up with three slightly different border greys.
 */
export default function Card({ as: Tag = 'div', className = '', children, ...rest }) {
  return (
    <Tag className={`card ${className}`.trim()} {...rest}>
      {children}
    </Tag>
  );
}
