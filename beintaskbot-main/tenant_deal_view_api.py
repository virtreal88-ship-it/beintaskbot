"""Deal preferences share the same authenticated identity and CSRF boundary."""
from dataclasses import dataclass
from tenant_task_view_api import TaskViewContext, handler


@dataclass(frozen=True)
class DealViewContext(TaskViewContext):
    invalid_message: str = 'Sövdələşmə mənbəyi düzgün deyil.'
    failure_message: str = 'Sövdələşmə görünüşü yüklənmədi və ya saxlanmadı.'
