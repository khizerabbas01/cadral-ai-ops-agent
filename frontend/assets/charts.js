// Hand-rolled SVG charts: sparkline, donut, column chart, line chart and ranked bars.
// Rules they follow: one axis per chart, solid hairline grid, a 2px surface gap between touching fills,
// labels in text colours (never the series colour), a hover + keyboard-focus tooltip on every plotted mark,
// and a screen-reader table twin so no value is reachable by hover alone.
import { bindTip, fmt, h, hideTip, onLeave, s, set, showTip, srTable } from "./lib.js";

// Categorical colours for the three specialist agents. Checked with a colour-vision-deficiency validator
// (all pairs pass under protan/deutan simulation); the legend beside each chart also names every series.
export const COLORS = { support: "#0d7353", orders: "#55c38d", escalation: "#9a90ea", pending: "#c5cdc9", brand: "#0f6b4f" };

let seq = 0;
const uid = () => `g${++seq}`;
const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;

/** Re-draws `draw(width, height)` whenever the container's width changes, so text stays crisp at any size. */
function responsive(draw, heightRem) {
  const box = h("div", { class: "chart", style: `height:${heightRem}rem` });
  let last = 0;
  const render = () => {
    const w = Math.round(box.clientWidth);
    if (!w || w === last) return;
    last = w;
    set(box, draw(w, Math.round(heightRem * remPx())));
  };
  const observer = new ResizeObserver(render);
  observer.observe(box);
  onLeave(() => observer.disconnect());
  setTimeout(render, 0); // first draw even in a background tab, where resize observers do not fire
  return box;
}

function niceScale(max, { ticks = 4, integer = false } = {}) {
  const raw = Math.max(max, 1e-9) / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  let step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  if (integer) step = Math.max(1, Math.ceil(step));
  const top = Math.ceil(max / step - 1e-9) * step || step;
  return { top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => +(i * step).toFixed(6)) };
}

function smoothPath(p) {
  if (p.length < 3) return `M${p.map((q) => q.join(",")).join("L")}`;
  let d = `M${p[0][0]},${p[0][1]}`;
  const t = 0.16;
  for (let i = 0; i < p.length - 1; i++) {
    const a = p[i - 1] || p[i], b = p[i], c = p[i + 1], e = p[i + 2] || c;
    d += `C${b[0] + (c[0] - a[0]) * t},${b[1] + (c[1] - a[1]) * t} ${c[0] - (e[0] - b[0]) * t},${c[1] - (e[1] - b[1]) * t} ${c[0]},${c[1]}`;
  }
  return d;
}

/** KPI trend line. Decorative (the tile states the value and the change), so it carries no tooltip. */
export function sparkline(values, { color = "#1f9d6b" } = {}) {
  const W = 104, H = 40, pad = 3;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", "aria-hidden": "true", preserveAspectRatio: "none" });
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) {
    svg.append(s("line", { x1: pad, x2: W - pad, y1: H - 8, y2: H - 8, stroke: "var(--line-strong)", "stroke-width": 2, "stroke-linecap": "round" }));
    return svg;
  }
  const ys = pts.map(([, v]) => v), min = Math.min(...ys), span = Math.max(...ys) - min;
  const first = pts[0][0], range = pts.at(-1)[0] - first;
  const xy = pts.map(([i, v]) => [pad + ((i - first) / range) * (W - pad * 2), span ? H - 7 - ((v - min) / span) * (H - 16) : H / 2]);
  const id = uid(), line = smoothPath(xy.map(([x, y]) => [+x.toFixed(1), +y.toFixed(1)]));
  svg.append(
    s("defs", {}, s("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.22 }), s("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }))),
    s("path", { d: `${line}L${xy.at(-1)[0]},${H}L${xy[0][0]},${H}Z`, fill: `url(#${id})` }),
    s("path", { d: line, fill: "none", stroke: color, "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
  return svg;
}

/** Donut with the total in the middle. segments: [{label, value, color}], drawn clockwise from 12 o'clock. */
export function donut(segments, { centerValue, centerLabel } = {}) {
  const size = 220, thickness = 40, r = (size - thickness) / 2, mid = size / 2, circ = 2 * Math.PI * r;
  const total = segments.reduce((sum, seg) => sum + seg.value, 0);
  const live = segments.filter((seg) => seg.value > 0);
  const gap = live.length > 1 ? 3 : 0;
  const svg = s("svg", { viewBox: `0 0 ${size} ${size}`, class: "donut", role: "img",
    "aria-label": `${centerValue} ${centerLabel}: ${segments.map((seg) => `${seg.label} ${seg.value}`).join(", ")}` });
  svg.append(s("circle", { cx: mid, cy: mid, r, fill: "none", stroke: "var(--track)", "stroke-width": thickness }));
  let offset = 0;
  for (const seg of live) {
    const len = (seg.value / total) * circ, dash = Math.max(1, len - gap);
    const arc = s("circle", { cx: mid, cy: mid, r, fill: "none", stroke: seg.color, "stroke-width": thickness, class: "donut-seg",
      "stroke-dasharray": `${dash} ${circ - dash}`, "stroke-dashoffset": -(offset + gap / 2), transform: `rotate(-90 ${mid} ${mid})` });
    bindTip(arc, () => [{ label: seg.label, value: `${fmt.num(seg.value)} · ${fmt.pct(seg.value / total)}`, color: seg.color }]);
    svg.append(arc);
    offset += len;
  }
  svg.append(s("text", { x: mid, y: mid - 2, class: "donut-value", "text-anchor": "middle" }, centerValue),
    s("text", { x: mid, y: mid + 22, class: "donut-label", "text-anchor": "middle" }, centerLabel));
  return svg;
}

/** Vertical bars over time. items: [{label, title, value}]. */
export function columnChart(items, { heightRem = 13.5, format = fmt.num, caption = "", unit = "", integer = true } = {}) {
  return responsive((w, ht) => {
    const R = remPx(), m = { top: 0.7 * R, right: 0.4 * R, bottom: 2 * R, left: 3 * R };
    const scale = niceScale(Math.max(1, ...items.map((d) => d.value)), { integer });
    const plotW = w - m.left - m.right, plotH = ht - m.top - m.bottom, base = m.top + plotH, band = plotW / items.length;
    const barW = Math.min(2.8 * R, band * 0.62), y = (v) => base - (v / scale.top) * plotH, id = uid();
    const svg = s("svg", { width: w, height: ht, viewBox: `0 0 ${w} ${ht}`, role: "img", "aria-label": caption });
    svg.append(s("defs", {}, s("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { offset: "0%", "stop-color": "#0c6648" }), s("stop", { offset: "100%", "stop-color": "#2f9c76" }))));
    for (const tick of scale.ticks) {
      svg.append(s("line", { x1: m.left, x2: w - m.right, y1: y(tick), y2: y(tick), class: tick ? "gridline" : "gridline base" }),
        s("text", { x: m.left - 0.7 * R, y: y(tick), class: "axis", "text-anchor": "end", "dominant-baseline": "middle" }, format(tick)));
    }
    const every = Math.max(1, Math.ceil(items.length / Math.max(1, Math.floor(plotW / (3.6 * R)))));
    items.forEach((d, i) => {
      const x0 = m.left + band * i + (band - barW) / 2, top = y(d.value), rad = Math.min(0.42 * R, barW / 2, base - top);
      const g = s("g", { class: "bar" });
      if (d.value > 0) g.append(s("path", { fill: `url(#${id})`, d: `M${x0},${base}V${top + rad}Q${x0},${top} ${x0 + rad},${top}H${x0 + barW - rad}Q${x0 + barW},${top} ${x0 + barW},${top + rad}V${base}Z` }));
      else g.append(s("rect", { x: x0, y: base - 2, width: barW, height: 2, class: "bar-zero" }));
      g.append(s("rect", { x: m.left + band * i, y: m.top, width: band, height: plotH, fill: "transparent" })); // hit area = the whole band
      bindTip(g, () => [{ label: unit, value: format(d.value) }], d.title || d.label);
      svg.append(g);
      if (i % every === 0) svg.append(s("text", { x: x0 + barW / 2, y: ht - 0.5 * R, class: "axis", "text-anchor": "middle" }, d.label));
    });
    return [svg, srTable(caption, ["Period", unit || "Value"], items.map((d) => [d.title || d.label, format(d.value)]))];
  }, heightRem);
}

/** One series over time with a crosshair tooltip. points: [{label, title, value | null}]. */
export function lineChart(points, { heightRem = 11, format = fmt.num, max, color = COLORS.brand, caption = "", unit = "" } = {}) {
  return responsive((w, ht) => {
    const R = remPx(), m = { top: 0.8 * R, right: 0.9 * R, bottom: 1.8 * R, left: 3.4 * R };
    const known = points.map((p, i) => ({ ...p, i })).filter((p) => p.value != null);
    const scale = max != null ? { top: max, ticks: [0, max / 2, max] } : niceScale(Math.max(1e-9, ...known.map((p) => p.value)), { ticks: 3 });
    const plotW = w - m.left - m.right, plotH = ht - m.top - m.bottom, base = m.top + plotH;
    const x = (i) => m.left + (points.length > 1 ? (i / (points.length - 1)) * plotW : plotW / 2), y = (v) => base - (v / scale.top) * plotH;
    const svg = s("svg", { width: w, height: ht, viewBox: `0 0 ${w} ${ht}`, role: "img", "aria-label": caption });
    for (const tick of scale.ticks) {
      svg.append(s("line", { x1: m.left, x2: w - m.right, y1: y(tick), y2: y(tick), class: tick ? "gridline" : "gridline base" }),
        s("text", { x: m.left - 0.7 * R, y: y(tick), class: "axis", "text-anchor": "end", "dominant-baseline": "middle" }, format(tick)));
    }
    if (!known.length) {
      svg.append(s("text", { x: m.left + plotW / 2, y: m.top + plotH / 2, class: "axis", "text-anchor": "middle" }, "No data in this period yet"));
      return [svg];
    }
    const xy = known.map((p) => [x(p.i), y(p.value)]), path = `M${xy.map((q) => q.map((n) => n.toFixed(1)).join(",")).join("L")}`;
    svg.append(s("path", { d: `${path}L${xy.at(-1)[0]},${base}L${xy[0][0]},${base}Z`, fill: color, opacity: 0.1 }),
      s("path", { d: path, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }),
      s("circle", { cx: xy.at(-1)[0], cy: xy.at(-1)[1], r: 4, fill: color, stroke: "var(--panel)", "stroke-width": 2 }));
    const labelAt = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
    for (const i of labelAt) svg.append(s("text", { x: x(i), y: ht - 0.4 * R, class: "axis", "text-anchor": i === 0 ? "start" : i === points.length - 1 ? "end" : "middle" }, points[i].label));
    // crosshair: the pointer only has to be near a date, never on the 2px line
    const hair = s("line", { y1: m.top, y2: base, class: "crosshair", visibility: "hidden" });
    const dot = s("circle", { r: 4.5, fill: color, stroke: "var(--panel)", "stroke-width": 2, visibility: "hidden" });
    const overlay = s("rect", { x: m.left, y: m.top, width: plotW, height: plotH, fill: "transparent" });
    overlay.addEventListener("pointermove", (e) => {
      const box = svg.getBoundingClientRect(), px = e.clientX - box.left;
      const p = known.reduce((best, cand) => (Math.abs(x(cand.i) - px) < Math.abs(x(best.i) - px) ? cand : best));
      hair.setAttribute("x1", x(p.i)); hair.setAttribute("x2", x(p.i)); hair.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(p.i)); dot.setAttribute("cy", y(p.value)); dot.setAttribute("visibility", "visible");
      showTip(box.left + x(p.i), box.top + y(p.value), [{ label: unit, value: format(p.value), color }], p.title || p.label);
    });
    overlay.addEventListener("pointerleave", () => { hair.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(); });
    svg.append(hair, dot, overlay);
    return [svg, srTable(caption, ["Date", unit || "Value"], known.map((p) => [p.title || p.label, format(p.value)]))];
  }, heightRem);
}

/** Ranked horizontal bars, one hue, largest first. pairs: [[label, value]].
 *  A full bar stands for at least 1 (counts and ratios); scaleToLargest is for amounts that are all below 1. */
export function rankedBars(pairs, { format = fmt.num, emptyText = "No data yet", color, scaleToLargest = false } = {}) {
  if (!pairs.length) return h("p", { class: "muted small" }, emptyText);
  const max = Math.max(scaleToLargest ? Number.MIN_VALUE : 1, ...pairs.map(([, v]) => v));
  return h("div", { class: "hbars" }, pairs.map(([label, value]) => h("div", { class: "hbar" },
    h("span", { class: "hbar-label", title: label }, label),
    h("span", { class: "hbar-track" }, h("i", { style: `width:${Math.max(1.5, (value / max) * 100)}%${color ? `;background:${color}` : ""}` })),
    h("b", {}, format(value)))));
}

/** A ratio against its whole (0-1). */
export function meter(value, { label } = {}) {
  return h("div", { class: "meter", role: "img", "aria-label": `${label || "value"} ${fmt.pct(value)}` },
    h("i", { style: `width:${Math.round((value || 0) * 100)}%` }));
}
