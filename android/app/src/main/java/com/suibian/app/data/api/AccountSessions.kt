package com.suibian.app.data.api

import android.content.Context
import com.suibian.app.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response

internal class AccountSession(val token: String, val username: String, val expiresAt: Long) {
    fun activeAt(now: Long) = now < expiresAt
}

@Singleton
class AccountSessions @Inject constructor(@ApplicationContext context: Context) {
    // The app disables backup. Keys are scoped by endpoint across debug APKs.
    private val origin = BuildConfig.API_BASE_URL.toHttpUrl()
    private val storage = context.getSharedPreferences("suibian_account", Context.MODE_PRIVATE)
    private val tokenKey = "$origin/token"
    private val usernameKey = "$origin/username"
    private val expiresKey = "$origin/expires_at"
    private val current = MutableStateFlow(
        storage.getString(tokenKey, null)?.let { token ->
            storage.getString(usernameKey, null)?.let { username ->
                AccountSession(token, username, storage.getLong(expiresKey, 0L))
                    .takeIf { it.activeAt(System.currentTimeMillis()) }
            }
        },
    )
    internal val session = current.asStateFlow()

    internal val credentialsAllowed = origin.isHttps ||
        BuildConfig.DEBUG && origin.host in setOf("127.0.0.1", "localhost", "10.0.2.2", "::1")

    @Synchronized
    internal fun save(session: AccountSession) {
        check(storage.edit().putString(tokenKey, session.token).putString(usernameKey, session.username)
            .putLong(expiresKey, session.expiresAt).commit())
        current.value = session
    }

    @Synchronized
    internal fun clearIfCurrent(expected: AccountSession) {
        if (current.value !== expected) return
        storage.edit().remove(tokenKey).remove(usernameKey).remove(expiresKey).apply()
        current.value = null
    }

    internal fun interceptor(): Interceptor = Interceptor { chain ->
        val request = chain.request()
        if (!credentialsAllowed || !isProtectedApi(request.url, origin)) return@Interceptor chain.proceed(request)
        val saved = current.value
        val valid = saved?.takeIf { it.activeAt(System.currentTimeMillis()) }
        if (valid == null && saved != null) clearIfCurrent(saved)
        val response: Response = chain.proceed(accountRequest(request, origin, valid?.token))
        if (response.code == 401 && valid != null) clearIfCurrent(valid)
        response
    }
}

internal fun isProtectedApi(request: HttpUrl, origin: HttpUrl): Boolean {
    if (request.scheme != origin.scheme || request.host != origin.host || request.port != origin.port) return false
    val path = request.encodedPath.removePrefix(origin.encodedPath)
    return path.startsWith("v1/") && path !in setOf("v1/health", "v1/version", "v1/auth/register", "v1/auth/login")
}

internal fun accountRequest(request: Request, origin: HttpUrl, token: String?): Request =
    if (token == null || !isProtectedApi(request.url, origin)) request
    else request.newBuilder().header("Authorization", "Bearer $token").build()
