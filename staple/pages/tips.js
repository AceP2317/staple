// The hover layer: any element with data-tip shows it on mouse hover or keyboard focus. On a touch screen one tap
// shows it and keeps it; the next tap anywhere closes it, and opens the spot it lands on if that is another one.
// A tap never cancels anything, so it still presses what it lands on. The first line is the value.
const tip = document.getElementById('tip')
let open = null // the spot showing: its element, the chart point if any, what opened it, and where on it
function place() {
  const r = open.el.getBoundingClientRect()
  if (!open.el.isConnected || (!r.width && !r.height)) return hide()
  const x = open.dx == null ? r.left : r.left + open.dx
  const y = open.dy == null ? null : r.top + open.dy
  let top = (y ?? r.top) - tip.offsetHeight - 10
  if (top < 4) top = (y ?? r.bottom) + 14
  tip.style.left = Math.max(4, Math.min(x, innerWidth - tip.offsetWidth - 8)) + 'px'
  tip.style.top = top + 'px'
}
function show(el, text, by, point, x, y) {
  const [head, ...rest] = text.split('\n')
  const strong = document.createElement('strong')
  strong.textContent = head
  const body = document.createElement('div')
  body.textContent = rest.join('\n')
  tip.replaceChildren(strong, ...(rest.length ? [body] : []))
  tip.hidden = false
  const r = el.getBoundingClientRect()
  open = { el, point, by, dx: x == null ? null : x - r.left, dy: y == null ? null : y - r.top }
  place()
}
function hide() {
  tip.hidden = true
  open = null
  document.querySelectorAll('.cross').forEach((c) => (c.style.opacity = 0))
}
const target = (e) => e.target.closest && e.target.closest('[data-tip]')

const curves = new Map()
document.querySelectorAll('svg[data-curve]').forEach((svg) => curves.set(svg, { d: JSON.parse(svg.dataset.curve), cross: svg.querySelector('.cross'), i: 2 }))
function nearest(svg, clientX) {
  const { d } = curves.get(svg)
  const r = svg.getBoundingClientRect()
  const xv = ((clientX - r.left) / r.width) * d.w
  return d.xs.reduce((b, x, j) => (Math.abs(x - xv) < Math.abs(d.xs[b] - xv) ? j : b), 0)
}
function at(svg, j) {
  const c = curves.get(svg)
  c.i = j
  c.cross.setAttribute('x1', c.d.xs[j]); c.cross.setAttribute('x2', c.d.xs[j]); c.cross.style.opacity = 1
  return c.d.tips[j]
}

// Mouse and pen: open on entering a spot, close on leaving it.
document.addEventListener('pointerover', (e) => {
  const el = e.pointerType !== 'touch' && target(e)
  if (el && open?.el !== el) show(el, el.dataset.tip, 'hover')
})
document.addEventListener('pointerout', (e) => {
  const el = e.pointerType !== 'touch' && target(e)
  if (el && open?.by === 'hover' && open.el === el && !el.contains(e.relatedTarget)) hide()
})
// Keyboard: the browser's own focus-visible test is true for a Tab and false for a tap, so a tap never opens one here.
document.addEventListener('focusin', (e) => {
  const el = e.target
  if (!el.matches || !el.matches(':focus-visible')) return
  if (curves.has(el)) return show(el, at(el, curves.get(el).i), 'focus', curves.get(el).i)
  const spot = target(e)
  if (spot) show(spot, spot.dataset.tip, 'focus')
})
document.addEventListener('focusout', () => { if (open?.by === 'focus') hide() })
// Finger: a tap is a press and lift within 10 px that the browser did not cancel to scroll.
let press = null
document.addEventListener('pointerdown', (e) => { press = e.pointerType === 'touch' ? [e.clientX, e.clientY] : null })
document.addEventListener('pointercancel', () => (press = null))
document.addEventListener('pointerup', (e) => {
  if (!press || e.pointerType !== 'touch') return
  const moved = Math.hypot(e.clientX - press[0], e.clientY - press[1])
  press = null
  if (moved > 10) return
  const svg = e.target.closest && e.target.closest('svg[data-curve]')
  const el = svg || target(e)
  const point = svg ? nearest(svg, e.clientX) : undefined
  const same = open && el && open.el === el && open.point === point
  if (open) hide()
  if (!el || same) return
  if (svg) show(svg, at(svg, point), 'tap', point, e.clientX + 14, e.clientY)
  else show(el, el.dataset.tip, 'tap')
})
// An open tip moves with its spot when anything scrolls, and closes if the spot is gone.
addEventListener('scroll', () => open && place(), { capture: true, passive: true })
addEventListener('resize', () => open && place())

curves.forEach((c, svg) => {
  svg.addEventListener('pointermove', (ev) => {
    if (ev.pointerType === 'touch') return
    const j = nearest(svg, ev.clientX)
    show(svg, at(svg, j), 'hover', j, ev.clientX + 14, ev.clientY)
  })
  svg.addEventListener('pointerleave', (ev) => { if (ev.pointerType !== 'touch' && open?.by === 'hover' && open.el === svg) hide() })
  svg.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return
    const j = Math.max(0, Math.min(c.d.ks.length - 1, c.i + (ev.key === 'ArrowRight' ? 1 : -1)))
    show(svg, at(svg, j), 'focus', j)
    ev.preventDefault()
  })
})

// The light/dark switch: every page opens light unless this browser saved dark, which the page's head applies first.
const themeBtn = document.querySelector('[data-theme-toggle]')
function setTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  themeBtn.textContent = dark ? 'Light mode' : 'Dark mode'
}
if (themeBtn) {
  setTheme(document.documentElement.dataset.theme === 'dark')
  themeBtn.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme !== 'dark'
    setTheme(dark)
    try { localStorage.setItem('staple-theme', dark ? 'dark' : 'light') } catch (err) { console.warn('Staple: the light/dark choice was not saved', err) }
  })
}
