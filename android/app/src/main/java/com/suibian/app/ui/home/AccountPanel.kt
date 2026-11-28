package com.suibian.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.suibian.app.data.api.AccountApi
import com.suibian.app.data.api.AccountCredentials
import com.suibian.app.data.api.AccountSessions
import com.suibian.app.data.api.rateLimitMessage
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import retrofit2.HttpException

@HiltViewModel
class AccountViewModel @Inject constructor(
    private val api: AccountApi,
    private val sessions: AccountSessions,
) : ViewModel() {
    internal val session = sessions.session
    internal val credentialsAllowed = sessions.credentialsAllowed
    private val _state = MutableStateFlow(AccountPanelState())
    internal val state = _state.asStateFlow()

    internal fun signIn(username: String, password: String, register: Boolean) {
        if (_state.value.busy || !credentialsAllowed) return
        val name = username.trim()
        if (!ACCOUNT_NAME.matches(name) || password.length !in 12..128) {
            _state.value = AccountPanelState(message = "用户名须为 3–32 位英文字母、数字或 _.-，密码须为 12–128 个字符")
            return
        }
        _state.value = AccountPanelState(busy = true)
        viewModelScope.launch {
            try {
                val credentials = AccountCredentials(name, password)
                val response = if (register) api.register(credentials) else api.login(credentials)
                withContext(Dispatchers.IO) { sessions.save(response.session(System.currentTimeMillis())) }
                _state.value = AccountPanelState()
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpException) {
                val message = when (e.code()) {
                    401 -> "用户名或密码错误"
                    403 -> "当前账号无权限"
                    409 -> "该用户名已被占用"
                    422 -> "账号格式不符合服务端规则"
                    429 -> e.rateLimitMessage()
                    404 -> "当前服务还未提供账号接口"
                    else -> "账号服务暂不可用，请重试"
                }
                _state.value = AccountPanelState(message = message)
            } catch (_: Exception) {
                _state.value = AccountPanelState(message = "暂时无法建立登录会话，请重试")
            }
        }
    }

    internal fun signOut() {
        val oldSession = session.value ?: return
        if (_state.value.busy) return
        _state.value = AccountPanelState(busy = true)
        viewModelScope.launch {
            val revoked = try {
                val response = api.logout()
                response.isSuccessful || response.code() == 401
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                false
            }
            withContext(Dispatchers.IO) { sessions.clearIfCurrent(oldSession) }
            _state.value = AccountPanelState(
                message = if (revoked) "已退出登录" else "本机已退出。服务端会话撤销尚未确认",
            )
        }
    }

    internal fun clearMessage() {
        if (!_state.value.busy) _state.value = AccountPanelState()
    }
}

private val ACCOUNT_NAME = Regex("[A-Za-z0-9_.-]{3,32}")
internal data class AccountPanelState(val busy: Boolean = false, val message: String? = null)

@Composable
internal fun AccountPanel(viewModel: AccountViewModel) {
    val session by viewModel.session.collectAsState()
    val state by viewModel.state.collectAsState()
    var open by remember { mutableStateOf(false) }

    val account = session
    LaunchedEffect(account) { if (account != null) open = false }
    if (account == null) {
        TextButton(onClick = {
            viewModel.clearMessage()
            open = true
        }, enabled = !state.busy) { Text("登录或注册") }
    } else {
        Text("账号 ${account.username}")
        TextButton(onClick = viewModel::signOut, enabled = !state.busy) { Text("退出登录") }
    }
    if (account != null || !open) {
        state.message?.let { Text(it) }
        return
    }

    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var register by remember { mutableStateOf(false) }
    val title = if (register) "注册" else "登录"
    val close = {
        if (!state.busy) {
            open = false
            viewModel.clearMessage()
        }
    }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("随变账号 · $title") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(username, { username = it }, enabled = !state.busy,
                    label = { Text("用户名") }, singleLine = true)
                OutlinedTextField(
                    password, { password = it }, enabled = !state.busy, label = { Text("密码") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                )
                TextButton(onClick = {
                    register = !register
                    viewModel.clearMessage()
                }, enabled = !state.busy) { Text(if (register) "已有账号，去登录" else "没有账号，去注册") }
                if (!viewModel.credentialsAllowed) Text("请使用 HTTPS 联调地址登录")
                state.message?.let { Text(it) }
            }
        },
        confirmButton = {
            TextButton(onClick = { viewModel.signIn(username, password, register) },
                enabled = !state.busy && viewModel.credentialsAllowed && username.isNotBlank() && password.isNotEmpty()) {
                Text(if (state.busy) "处理中…" else title)
            }
        },
        dismissButton = { TextButton(onClick = close, enabled = !state.busy) { Text("取消") } },
    )
}
