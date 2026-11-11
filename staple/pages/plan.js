// The plan screen: which day, which technician, which call is open, and the night plan or the day as it ran.
// Every day is already in the page; this only shows and hides it.
const page = document.body
const all = (sel, root = document) => [...root.querySelectorAll(sel)]
const callBox = document.querySelector('.call-open')
const nothingOpen = callBox.textContent
const calm = matchMedia('(prefers-reduced-motion: reduce)')
const board = () => all('.board').find((b) => b.offsetParent !== null)
const findCall = (job) => board()?.querySelector(`.blk[data-tip^="Call ${job} "]`)

function pickDay(day) {
  all('.day-btn').forEach((b) => b.setAttribute('aria-pressed', b.dataset.day === day))
  all('[data-day]:not(.day-btn)').forEach((s) => (s.hidden = s.dataset.day !== day))
  document.querySelector('.day-name').textContent = document.querySelector(`.day-btn[data-day="${day}"]`).dataset.name
  closeCall()
  pickTech(page.dataset.chosen)
  rove()
}
function pickTech(tech, sheet) {
  page.dataset.chosen = tech
  all('.row[data-tech], .rt, .todo-item[data-tech]').forEach((el) => el.classList.toggle('sel', el.dataset.tech === tech))
  all('.map').forEach((m) => m.classList.add('has-sel'))
  all('[data-tech-card]').forEach((c) => (c.hidden = c.dataset.techCard !== tech))
  all('[data-sheet]').forEach((s) => {
    s.hidden = s.dataset.sheet !== tech
    if (sheet && !s.hidden) s.open = true
  })
}
// A call opens beside the board as the lines of its own hint, so the panel and the hint can never disagree.
function openCall(blk) {
  const [head, ...lines] = blk.dataset.tip.split('\n')
  const title = Object.assign(document.createElement('h3'), { textContent: head })
  callBox.replaceChildren(title, ...lines.map((line) => Object.assign(document.createElement('p'), { textContent: line })))
  callBox.dataset.call = head.match(/^Call (\d+)/)[1]
  all('.blk.on').forEach((b) => b.classList.remove('on'))
  blk.classList.add('on')
  pickTech(blk.closest('.row').dataset.tech)
}
function closeCall() {
  delete callBox.dataset.call
  all('.blk.on').forEach((b) => b.classList.remove('on'))
  callBox.replaceChildren(Object.assign(document.createElement('p'), { className: 'empty', textContent: nothingOpen }))
}
function setView(view) {
  page.dataset.view = view
  all('.vbtn').forEach((b) => b.setAttribute('aria-pressed', b.dataset.view === view))
  const blk = callBox.dataset.call && findCall(callBox.dataset.call)
  if (blk) openCall(blk)
  else closeCall()
  rove()
}
// The board is one stop in the Tab order. Inside it the arrow keys move between calls and technicians.
function rove(to) {
  const b = board()
  if (!b) return
  const spots = all('.who, .blk', b)
  const at = to || spots.find((s) => s.tabIndex === 0) || b.querySelector('.blk') || spots[0]
  spots.forEach((s) => (s.tabIndex = s === at ? 0 : -1))
  if (to) to.focus()
}
function arrow(el, key) {
  const row = el.closest('.row')
  const here = all('.who, .blk', row)
  const i = here.indexOf(el)
  if (key === 'ArrowRight') return here[i + 1]
  if (key === 'ArrowLeft') return here[i - 1]
  if (key === 'Home') return here[0]
  if (key === 'End') return here[here.length - 1]
  const rows = all('.row[data-tech]', row.parentNode)
  const next = rows[rows.indexOf(row) + (key === 'ArrowDown' ? 1 : -1)]
  if (!next) return null
  const x = el.getBoundingClientRect().left
  const off = (b) => Math.abs(b.getBoundingClientRect().left - x)
  const calls = el.matches('.who') ? [] : all('.blk', next)
  return calls.reduce((best, b) => (best && off(best) <= off(b) ? best : b), null) || next.querySelector('.who')
}
function flash(name) {
  const card = document.getElementById('proof-' + name)
  if (!card) return
  card.classList.remove('flash')
  void card.offsetWidth
  card.classList.add('flash')
}
// A click made in a list far from the board brings the plan into view. A finger's tap does not, because that tap
// is also how a phone shows the item's hint.
function reveal(ev) {
  if (ev.pointerType === 'touch') return
  document.getElementById('plan').scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth', block: 'start' })
}
document.addEventListener('click', (ev) => {
  const t = ev.target
  const day = t.closest('.day-btn')
  if (day) return pickDay(day.dataset.day)
  const proof = t.closest('[data-proof]')
  if (proof) return flash(proof.dataset.proof)
  const view = t.closest('[data-view]')
  if (view) setView(view.dataset.view)
  const blk = t.closest('.blk')
  if (blk) {
    rove(blk)
    return blk.classList.contains('on') ? closeCall() : openCall(blk)
  }
  const item = t.closest('.todo-item, .ask, .evt')
  const call = item && item.dataset.call && findCall(item.dataset.call)
  const tech = t.closest('[data-tech]')
  if (call) openCall(call)
  else if (tech) pickTech(tech.dataset.tech, !!t.closest('.who, .todo-item'))
  if (item && (call || tech)) reveal(ev)
})
document.addEventListener('keydown', (ev) => {
  const el = ev.target
  if (!el.matches || !el.matches('.blk, .who, .todo-item, .ask, .evt')) return
  if (ev.key === 'Enter' || ev.key === ' ') {
    ev.preventDefault()
    return el.click()
  }
  if (!el.matches('.blk, .who') || !/^(Arrow|Home$|End$)/.test(ev.key)) return
  const to = arrow(el, ev.key)
  ev.preventDefault()
  if (to) rove(to)
})
// Printing shows every load sheet and table open, in the light scheme, and puts the page back afterwards.
let before = null
addEventListener('beforeprint', () => {
  before = { theme: document.documentElement.dataset.theme, shut: all('details:not([open])') }
  document.documentElement.dataset.theme = 'light'
  before.shut.forEach((d) => (d.open = true))
})
addEventListener('afterprint', () => {
  if (!before) return
  document.documentElement.dataset.theme = before.theme
  before.shut.forEach((d) => (d.open = false))
  before = null
})
all('.blk').forEach((b) => {
  b.setAttribute('role', 'button')
  b.setAttribute('aria-label', b.dataset.tip.split('\n')[0])
})
all('.who').forEach((w) => w.setAttribute('role', 'button'))
all('.board').forEach((b) => all('.who, .blk', b).forEach((s) => { if (!s.hasAttribute('tabindex')) s.tabIndex = -1 }))
pickTech(page.dataset.chosen)
