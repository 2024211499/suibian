package com.suibian.app.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.HttpException
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.POST

interface AccountApi {
    @POST("v1/auth/register")
    suspend fun register(@Body credentials: AccountCredentials): AccountResponse

    @POST("v1/auth/login")
    suspend fun login(@Body credentials: AccountCredentials): AccountResponse

    @POST("v1/auth/logout")
    suspend fun logout(): Response<Unit>
}

@Serializable
class AccountCredentials(val username: String, val password: String)

@Serializable
class AccountResponse(
    @SerialName("access_token") private val token: String,
    @SerialName("token_type") private val tokenType: String,
    @SerialName("expires_in") private val expiresIn: Long,
    private val user: AccountUser,
) {
    internal fun session(now: Long): AccountSession {
        require(tokenType.equals("bearer", ignoreCase = true) && token.isNotBlank() && expiresIn in 1..86_400)
        return AccountSession(token, user.username, now + expiresIn * 1_000)
    }
}

@Serializable
class AccountUser(val username: String)

internal fun HttpException.rateLimitMessage(): String {
    val retryAfter = response()?.headers()?.get("Retry-After")?.toLongOrNull()?.takeIf { it > 0 }
    return retryAfter?.let { "请求太频繁，请在 ${it} 秒后重试" } ?: "请求太频繁，请稍后重试"
}
