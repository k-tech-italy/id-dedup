import "./style.css"
import htmx from "htmx.org"
import Alpine from "alpinejs"
import Sortable from "sortablejs"
import replaceElement from "lucide/dist/esm/replaceElement.mjs"
import Search from "lucide/dist/esm/icons/search.mjs"
import X from "lucide/dist/esm/icons/x.mjs"
import ChevronLeft from "lucide/dist/esm/icons/chevron-left.mjs"
import ChevronRight from "lucide/dist/esm/icons/chevron-right.mjs"

window.htmx = htmx
window.Alpine = Alpine
window.Sortable = Sortable

// Deep lucide imports keep unused icons out of the bundle: webpack does not
// tree-shake in development mode, so importing from the "lucide" barrel
// would pull in all ~1800 icon modules.
const icons = { Search, X, ChevronLeft, ChevronRight }

const renderIcons = () => {
  document.querySelectorAll("i[data-lucide]").forEach((element) => {
    replaceElement(element, { nameAttr: "data-lucide", icons, attrs: {} })
  })
}

document.addEventListener("DOMContentLoaded", renderIcons)
htmx.on("htmx:afterSwap", renderIcons)
