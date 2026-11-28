package com.suibian.app.deployment

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.suibian.app.data.db.ActionLogEntity
import com.suibian.app.executor.ActionType
import com.suibian.app.executor.WritableCalendar
import com.suibian.app.ui.home.HomePlan
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalTime
import java.time.ZoneId
import javax.inject.Inject

@HiltViewModel
class DeploymentViewModel @Inject constructor(private val deployment: PlanDeployment) : ViewModel() {
    private val _editor = MutableStateFlow<DeploymentEditor?>(null)
    val editor = _editor.asStateFlow()

    fun open(plan: HomePlan, trialDeparture: Int) {
        val summary = plan.summary
        val editor = DeploymentEditor(
            planId = summary.id,
            version = summary.version,
            form = DeploymentForm(
                title = summary.title.orEmpty(), date = summary.date.orEmpty(), zone = ZoneId.systemDefault().id,
                start = "", end = "", reminder = LocalTime.ofSecondOfDay(trialDeparture * 60L).toString(),
            ),
            busy = true,
        )
        _editor.value = editor
        viewModelScope.launch {
            try {
                val rows = deployment.actions(editor.planId, editor.version)
                val form = deployment.recordedSpec(rows)?.toForm() ?: editor.form
                val allowed = deployment.canAccessCalendar()
                val calendars = if (allowed) deployment.writableCalendars() else emptyList()
                _editor.value = editor.copy(
                    form = form, records = rows, calendars = calendars, calendarPermission = allowed,
                    busy = false,
                )
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                _editor.value = editor.copy(busy = false, message = "暂时无法读取部署记录或日历")
            }
        }
    }

    fun close() {
        if (_editor.value?.busy != true) _editor.value = null
    }

    fun changeForm(change: (DeploymentForm) -> DeploymentForm) {
        _editor.update { current -> current?.takeUnless { it.busy }?.let { it.copy(form = change(it.form), message = null) } ?: current }
    }

    fun refreshCalendars() {
        val current = _editor.value ?: return
        if (current.busy) return
        _editor.value = current.copy(busy = true, message = null)
        viewModelScope.launch {
            try {
                val allowed = deployment.canAccessCalendar()
                val calendars = if (allowed) deployment.writableCalendars() else emptyList()
                _editor.value = current.copy(busy = false, calendarPermission = allowed, calendars = calendars)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                _editor.value = current.copy(busy = false, message = "无法读取可写日历，可检查授权后重试")
            }
        }
    }

    fun preparePreview(only: ActionType? = null) {
        val current = _editor.value ?: return
        if (current.busy) return
        val preview = runCatching { current.form.toSpec(current.planId, current.version, System.currentTimeMillis()) }
        _editor.value = preview.fold(
            onSuccess = { current.copy(preview = DeploymentPreview(it, only), message = null) },
            onFailure = { current.copy(message = it.message ?: "请检查日期和时段") },
        )
    }

    fun dismissPreview() = _editor.update { it?.copy(preview = null) }

    fun confirm() {
        val current = _editor.value ?: return
        val preview = current.preview ?: return
        if (current.busy) return
        _editor.value = current.copy(busy = true, preview = null, message = null)
        viewModelScope.launch {
            try {
                val rows = deployment.confirm(preview.spec, preview.only)
                _editor.value = current.copy(busy = false, preview = null, records = rows, message = null)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val rows = runCatching { deployment.actions(current.planId, current.version) }.getOrDefault(current.records)
                val message = if (e is IllegalArgumentException || e is IllegalStateException) e.message else null
                _editor.value = current.copy(
                    busy = false, preview = null, records = rows,
                    message = message ?: "部署中断，请核对操作记录后重试",
                )
            }
        }
    }
}

data class DeploymentEditor(
    val planId: String,
    val version: Int,
    val form: DeploymentForm,
    val calendars: List<WritableCalendar> = emptyList(),
    val calendarPermission: Boolean = false,
    val records: List<ActionLogEntity> = emptyList(),
    val busy: Boolean = false,
    val preview: DeploymentPreview? = null,
    val message: String? = null,
)

data class DeploymentPreview(val spec: DeploymentSpec, val only: ActionType?)
