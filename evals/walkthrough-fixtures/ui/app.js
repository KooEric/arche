const pickup = document.querySelector('#pickup');
const undo = document.querySelector('#undo');
const status = document.querySelector('#status');
function display(done) {
  status.textContent = done ? '수령 완료' : '대기 중';
  pickup.hidden = done;
  undo.hidden = !done;
}
pickup.onclick = () => display(true);
undo.onclick = () => display(false);
