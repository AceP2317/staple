// The plan screen: which day, which technician, the night plan or the day as it ran, and on a phone plan or proof.
// Every day is already in the page; this only shows and hides it.
const page = document.body
const shown = (el) => el && el.offsetParent !== null
function pickDay(day) {
  document.querySelectorAll('.day-btn').forEach((b) => b.classList.toggle('on', b.dataset.day === day))
  document.querySelectorAll('.day, .day-proof').forEach((s) => (s.hidden = s.dataset.day !== day))
  pickTech(page.dataset.chosen)
}
function pickTech(tech, reveal) {
  page.dataset.chosen = tech
  document.querySelectorAll('.row[data-tech], .rt').forEach((el) => el.classList.toggle('sel', el.dataset.tech === tech))
  document.querySelectorAll('.map').forEach((m) => m.classList.add('has-sel'))
  document.querySelectorAll('[data-sheet]').forEach((s) => (s.hidden = s.dataset.sheet !== tech))
  document.querySelectorAll('[data-tech-card]').forEach((c) => (c.hidden = c.dataset.techCard !== tech))
  number(tech)
  const card = document.querySelector(`[data-tech-card="${tech}"]`)
  if (reveal && shown(card)) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}
function number(tech) {
  document.querySelectorAll('.map .num').forEach((n) => n.remove())
  document.querySelectorAll(`.v-night .rt[data-tech="${tech}"]`).forEach((g) => {
    g.querySelectorAll('.stop').forEach((stop, i) => {
      const n = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      n.setAttribute('class', 'num')
      n.setAttribute('x', stop.getAttribute('cx'))
      n.setAttribute('y', +stop.getAttribute('cy') + 2)
      n.textContent = i + 1
      g.appendChild(n)
    })
  })
}
function setView(view) {
  page.dataset.view = view
  document.querySelectorAll('.vbtn').forEach((b) => b.classList.toggle('on', b.dataset.view === view))
}
function showProof(name) {
  const card = document.getElementById('proof-' + name)
  if (!shown(card)) return
  card.closest('.pane').scrollTo({ top: card.offsetTop - 30, behavior: 'smooth' })
  card.classList.remove('flash')
  void card.offsetWidth
  card.classList.add('flash')
}
document.addEventListener('click', (ev) => {
  const t = ev.target
  const day = t.closest('.day-btn')
  if (day) return pickDay(day.dataset.day)
  const side = t.closest('.side-btn')
  if (side) {
    page.dataset.side = side.dataset.side
    document.querySelectorAll('.side-btn').forEach((b) => b.classList.toggle('on', b === side))
    return scrollTo(0, 0)
  }
  const view = t.closest('[data-view]')
  if (view) setView(view.dataset.view)
  const tech = t.closest('[data-tech]')
  if (tech) pickTech(tech.dataset.tech, true)
  const proof = t.closest('[data-proof]')
  if (proof) showProof(proof.dataset.proof)
})
document.addEventListener('keydown', (ev) => {
  const el = ev.target
  if ((ev.key === 'Enter' || ev.key === ' ') && !el.matches('button, a') && el.closest('[data-tech], [data-proof], [data-view]')) {
    ev.preventDefault()
    el.click()
  }
})
pickTech(page.dataset.chosen)
