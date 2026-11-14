// Calendar-day grouping in the viewer's timezone, never elapsed 24-hour windows.
export function taskColumns(rows, now = new Date()) {
  const day = value => { const d = new Date(value); return Number.isNaN(d.getTime()) ? null : Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000; };
  const today = day(now);
  const groups = ['overdue', 'today', 'tomorrow', 'upcoming', 'later', 'undated', 'completed'].map(key => ({key, rows: []}));
  for (const task of rows) {
    const due = task.due_at ? day(task.due_at) : null;
    const delta = due === null ? null : due - today;
    const key = task.status === 'completed' || task.completed ? 'completed' : delta === null ? 'undated' : delta < 0 ? 'overdue' : delta === 0 ? 'today' : delta === 1 ? 'tomorrow' : delta <= 7 ? 'upcoming' : 'later';
    groups.find(group => group.key === key).rows.push(task);
  }
  return groups.filter((group, index) => index < 4 || group.rows.length);
}
export const taskBoardLabels = {
  ru: {kanban:'Канбан', table:'Таблица', overdue:'Просроченные', today:'Сегодня', tomorrow:'Завтра', upcoming:'Ближайшие 7 дней', later:'Позже', undated:'Без срока', completed:'Завершённые', empty:'Нет задач', count:'Задач', view:'Вид задач'},
  az: {kanban:'Kanban', table:'Cədvəl', overdue:'Gecikmiş', today:'Bu gün', tomorrow:'Sabah', upcoming:'Növbəti 7 gün', later:'Daha sonra', undated:'Tarixsiz', completed:'Tamamlanmış', empty:'Tapşırıq yoxdur', count:'Tapşırıq', view:'Tapşırıq görünüşü'},
  en: {kanban:'Kanban', table:'Table', overdue:'Overdue', today:'Today', tomorrow:'Tomorrow', upcoming:'Next 7 days', later:'Later', undated:'No due date', completed:'Completed', empty:'No tasks', count:'Tasks', view:'Task view'},
};
