const button = document.getElementById("toggle-pass");
const field = document.getElementById("password");
if (button && field) {
  button.addEventListener("click", () => {
    const show = field.type === "password";
    field.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", show ? "true" : "false");
    button.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
    button.title = show ? "Скрыть пароль" : "Показать пароль";
    const iconShow = button.querySelector(".icon-show");
    const iconHide = button.querySelector(".icon-hide");
    if (iconShow && iconHide) {
      iconShow.hidden = show;
      iconHide.hidden = !show;
    }
  });
}
