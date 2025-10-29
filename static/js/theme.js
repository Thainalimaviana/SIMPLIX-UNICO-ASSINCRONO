document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const toggleTheme = document.getElementById("toggle-theme");
  const isDark = localStorage.getItem("modoEscuro") === "true";

  if (isDark) {
    body.classList.add("dark-mode");
    if (toggleTheme) {
      const icon = toggleTheme.querySelector("i");
      if (icon) {
        icon.classList.remove("fa-moon");
        icon.classList.add("fa-sun");
      }
    }
  }

  if (toggleTheme) {
    toggleTheme.addEventListener("click", () => {
      const modoAtivo = body.classList.toggle("dark-mode");
      localStorage.setItem("modoEscuro", modoAtivo);
      const icon = toggleTheme.querySelector("i");
      if (icon) {
        icon.classList.toggle("fa-moon", !modoAtivo);
        icon.classList.toggle("fa-sun", modoAtivo);
      }
    });
  }
});
