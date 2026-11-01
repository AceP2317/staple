// The hover layer: any element with data-tip shows it on hover, keyboard focus or tap. The first line is the value.
const tip = document.getElementById('tip')
let current = null
function show(text, x, y, el) {
  const [head, ...rest] = text.split('\n')
  const strong = document.createElement('strong')
  strong.textContent = head
  const body = document.createElement('div')
  body.textContent = rest.join('\n')
  tip.replaceChildren(strong, ...(rest.length ? [body] : []))
  tip.hidden = false
  const r = el.getBoundingClientRect()
  const left = x ?? r.left
  let top = (y ?? r.top) - tip.offsetHeight - 10
  if (top < 4) top = (y ?? r.bottom) + 14
  tip.style.left = Math.max(4, Math.min(left, innerWidth - tip.offsetWidth - 8)) + 'px'
  tip.style.top = top + 'px'
}
function hide() {
  tip.hidden = true
  current = null
  document.querySelectorAll('.cross').forEach((c) => (c.style.opacity = 0))
}
const target = (e) => e.target.closest && e.target.closest('[data-tip]')
document.addEventListener('pointerover', (e) => {
  const el = target(e)
  if (el && el !== current) { current = el; show(el.dataset.tip, null, null, el) }
})
document.addEventListener('pointerout', (e) => {
  const el = target(e)
  if (el && !el.contains(e.relatedTarget)) hide()
})
document.addEventListener('focusin', (e) => { const el = target(e); if (el) { current = el; show(el.dataset.tip, null, null, el) } })
document.addEventListener('focusout', hide)
document.querySelectorAll('svg[data-curve]').forEach((svg) => {
  const d = JSON.parse(svg.dataset.curve)
  const cross = svg.querySelector('.cross')
  let i = 2
  const at = (j) => {
    cross.setAttribute('x1', d.xs[j]); cross.setAttribute('x2', d.xs[j]); cross.style.opacity = 1
    return d.tips[j]
  }
  svg.addEventListener('pointermove', (ev) => {
    const r = svg.getBoundingClientRect()
    const xv = ((ev.clientX - r.left) / r.width) * d.w
    i = d.xs.reduce((b, x, j) => (Math.abs(x - xv) < Math.abs(d.xs[b] - xv) ? j : b), 0)
    show(at(i), ev.clientX + 14, ev.clientY, svg)
  })
  svg.addEventListener('pointerleave', hide)
  svg.addEventListener('focus', () => show(at(i), null, null, svg))
  svg.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return
    i = Math.max(0, Math.min(d.ks.length - 1, i + (ev.key === 'ArrowRight' ? 1 : -1)))
    show(at(i), null, null, svg)
    ev.preventDefault()
  })
})
