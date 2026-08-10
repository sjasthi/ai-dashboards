import { useEffect, useRef } from 'react';
import { PLOT_CONFIG, buildLayout } from '../../chartLayout';

/**
 * Renders a server-built Plotly figure.
 *
 * Plotly is loaded once as a global by index.html; do not add a second dynamic
 * loader here (risks a version mismatch).
 */
export default function Plot({ chart, stats, compact = false, className }) {
  const ref = useRef(null);

  useEffect(() => {
    const node = ref.current;
    const Plotly = window.Plotly;
    if (!chart || !node || !Plotly) return undefined;

    const layout = buildLayout(chart, stats, { compact });

    // A sideways bar chart asks for a height based on how many bars it has. Plotly's
    // own responsive handler re-fits the plot to its container on every resize and
    // drops layout.height when it does, so the figure would snap back to the CSS
    // min-height and re-crowd the bars. Putting the number on the container instead
    // makes it the thing Plotly resizes *to*, so it survives.
    node.style.height = layout.height ? `${layout.height}px` : '';

    Plotly.newPlot(node, chart.data, layout, PLOT_CONFIG);

    // Tab switches unmount this node with a live plot still attached to it; without
    // the purge, Plotly keeps its internal references and the listeners leak.
    return () => Plotly.purge(node);
  }, [chart, stats, compact]);

  return <div ref={ref} className={className} />;
}
