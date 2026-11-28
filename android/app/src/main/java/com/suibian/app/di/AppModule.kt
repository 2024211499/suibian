package com.suibian.app.di

import android.content.Context
import androidx.room.Room
import com.suibian.app.BuildConfig
import com.suibian.app.background.AlarmScheduler
import com.suibian.app.data.api.SuibianApi
import com.suibian.app.data.db.AppDatabase
import com.suibian.app.data.db.MIGRATION_1_2
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun json(): Json = Json { ignoreUnknownKeys = true }

    @Provides
    @Singleton
    fun okHttp(): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    @Provides
    @Singleton
    fun retrofit(client: OkHttpClient, json: Json): Retrofit = Retrofit.Builder()
        // 调试包可通过 suibianApiBaseUrl 指定 USB 转发、局域网或联调服务地址。
        .baseUrl(BuildConfig.API_BASE_URL)
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()

    @Provides
    @Singleton
    fun suibianApi(retrofit: Retrofit): SuibianApi = retrofit.create(SuibianApi::class.java)

    @Provides
    @Singleton
    fun appDatabase(@ApplicationContext context: Context): AppDatabase =
        Room.databaseBuilder(context, AppDatabase::class.java, "suibian.db")
            .addMigrations(MIGRATION_1_2)
            .build()

    @Provides
    @Singleton
    fun alarmDao(db: AppDatabase): com.suibian.app.data.db.AlarmDao = db.alarmDao()

    @Provides
    @Singleton
    fun alarmScheduler(
        @ApplicationContext context: Context,
        alarmDao: com.suibian.app.data.db.AlarmDao,
    ): AlarmScheduler = AlarmScheduler(context, alarmDao)
}
