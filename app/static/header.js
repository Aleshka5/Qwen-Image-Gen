  (function () {
    var JITTER_FLOOR = 6
    var TRAVEL_THRESHOLD = 24
    var TOP_AT = 8
    var HIDE_BELOW = 96
    var root = document.documentElement
    root.dataset.header = 'top'

    var last = Math.max(0, window.scrollY)
    var acc = 0
    var frame = 0

    function headerHasFocus() {
      var active = document.activeElement
      if (!active) return false
      var header = document.querySelector('.header')
      return header !== null && header.contains(active)
    }

    function apply() {
      frame = 0
      var y = Math.max(0, window.scrollY)

      if (y < TOP_AT) {
        root.dataset.header = 'top'
        last = y
        acc = 0
        return
      }
      if (headerHasFocus()) {
        root.dataset.header = 'condensed'
        last = y
        acc = 0
        return
      }

      var d = y - last
      if (Math.abs(d) < JITTER_FLOOR) return
      acc = Math.sign(d) === Math.sign(acc) ? acc + d : d
      last = y

      if (acc > TRAVEL_THRESHOLD && y > HIDE_BELOW) root.dataset.header = 'hidden'
      else if (acc < -TRAVEL_THRESHOLD) root.dataset.header = 'condensed'
    }

    window.addEventListener(
      'scroll',
      function () {
        if (frame === 0) frame = window.requestAnimationFrame(apply)
      },
      { passive: true },
    )
  })()

;(function () {
  var link = document.getElementById('sign-in')
  if (!link) return
  var host = location.hostname
  if (location.protocol === 'https:' && (host === 'filenkov.store' || host.endsWith('.filenkov.store'))) {
    link.href = 'https://filenkov.store/auth?return_to=' + encodeURIComponent(location.href)
  }
})()
