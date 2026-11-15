package com.scaffapps.textshare;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;

/**
 * Hears of what others share with this phone the moment they share it, also while the app is closed: one request is
 * kept open to the computer that keeps the history (it answers as soon as something new is there for this phone, or
 * after 45 seconds with nothing), then asked again at once. No Google services and no internet: only the local
 * network. Android allows that only to a "foreground service", which must show a permanent notice; it sits on a
 * silent channel that can be hidden. Each new item becomes a notification with Copy / Save buttons (Actions).
 */
public class Listen extends Service {
    static final String CHANNEL_ON = "connected", CHANNEL_SHARED = "shared";
    private static final int NOTICE_ID = 8765;
    static final int WAIT_SECONDS = 45;

    private volatile boolean running;
    private Thread loop;

    /** Start (or keep) listening once Instant Share is set up. Safe to call often. */
    static void start(Context c) {
        if (!Net.paired(c) || Net.prefs(c).getBoolean("stopped", false)) return;     // not joined, or Quit
        try {
            c.startForegroundService(new Intent(c, Listen.class));
            Update.daily(c);
        } catch (RuntimeException notAllowedNow) {
            // Android refuses from the background unless battery optimisation is off; the app's next start retries
        }
    }

    static void restart(Context c) {
        c.stopService(new Intent(c, Listen.class));
        start(c);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        notice();
        running = true;
        if (loop == null || !loop.isAlive()) {
            loop = new Thread(this::listen, "textshare-listen");
            loop.start();
        }
        return START_STICKY;
    }

    private void listen() {
        long backoff = 2000;
        while (running) {
            String base = Net.base(this);
            if (!Net.paired(this) || Net.prefs(this).getBoolean("stopped", false)) { stopSelf(); return; }
            String key = "after@" + base;                          // what was last seen, per computer
            long after = Net.prefs(this).getLong(key, -1);
            HttpURLConnection con = null;
            try {
                con = Net.open(this, base + "/api/events" + (after < 0 ? "" : "?after=" + after + "&wait=" + WAIT_SECONDS),
                        (WAIT_SECONDS + 20) * 1000);
                if (con.getResponseCode() == 401) { Net.forgetKey(this); stopSelf(); return; }   // removed there: join again
                if (con.getResponseCode() != 200) throw new java.io.IOException("answered " + con.getResponseCode());
                JSONObject answer = new JSONObject(new String(Net.bytes(con.getInputStream()), StandardCharsets.UTF_8));
                JSONArray items = answer.getJSONArray("items");
                if (after >= 0 && items.length() > 0 && !MainActivity.foreground) {
                    for (int i = Math.min(items.length(), 5) - 1; i >= 0; i--) post(this, base, items.getJSONObject(i));
                }
                Net.prefs(this).edit().putLong(key, Math.max(after, answer.getLong("newest"))).apply();
                backoff = 2000;
            } catch (Exception away) {
                try { Thread.sleep(backoff); } catch (InterruptedException e) { return; }
                backoff = Math.min(backoff * 2, 60000);            // computer off or no Wi-Fi: back off, up to a minute
            } finally {
                if (con != null) con.disconnect();
            }
        }
    }

    /** One shared item as a notification: who, a preview (the picture for a photo), and what can be done with it. */
    static void post(Context c, String base, JSONObject it) throws Exception {
        NotificationManager nm = (NotificationManager) c.getSystemService(NOTIFICATION_SERVICE);
        if (!nm.areNotificationsEnabled()) return;
        if (nm.getNotificationChannel(CHANNEL_SHARED) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_SHARED, "Shared with this phone", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Text, photos and PDFs other devices share, with Copy and Save");
            nm.createNotificationChannel(ch);
        }
        int id = (int) it.getLong("id");
        JSONObject file = it.optJSONObject("file");
        boolean image = file != null && file.optString("type").startsWith("image/");
        boolean hasText = it.optInt("size") > 0;
        String who = it.optString("device", "Someone");
        String title = who + (it.optJSONArray("to") != null ? " shared with you" : " shared");
        String body = hasText ? it.optString("text") : file != null ? file.optString("name") : "";

        PendingIntent open = PendingIntent.getActivity(c, id, new Intent(c, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = new Notification.Builder(c, CHANNEL_SHARED)
                .setSmallIcon(R.drawable.ic_stat_share).setColor(0xFF5B2C83)
                .setContentTitle(title).setContentText(body)
                .setContentIntent(open).setAutoCancel(true).setShowWhen(true).setWhen(it.optLong("at", System.currentTimeMillis()));
        if (image) {
            Bitmap picture = null;
            try { picture = thumbnail(Net.get(c, base + file.getString("url"))); } catch (Exception ignored) {}
            if (picture != null) b.setLargeIcon(picture).setStyle(new Notification.BigPictureStyle().bigPicture(picture)
                    .setSummaryText(hasText ? it.optString("text") : file.optString("name")));
        } else {
            b.setStyle(new Notification.BigTextStyle().bigText(body));
        }
        if (hasText) b.addAction(action(c, id, Actions.COPY_TEXT, "Copy", it, base));
        if (image) b.addAction(action(c, id, Actions.COPY_PHOTO, hasText ? "Copy photo" : "Copy", it, base));
        if (file != null) b.addAction(action(c, id, Actions.SAVE, "Save", it, base));
        if (file != null && !image) {
            PendingIntent view = PendingIntent.getActivity(c, id * 4 + 3, new Intent(c, MainActivity.class)
                    .putExtra(MainActivity.EXTRA_VIEW_URL, base + file.getString("url"))
                    .putExtra(MainActivity.EXTRA_VIEW_NAME, file.getString("name"))
                    .putExtra(Actions.EXTRA_NOTIFICATION, id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP),
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            b.addAction(new Notification.Action.Builder(Icon.createWithResource(c, R.drawable.ic_stat_share), "Open", view).build());
        }
        try { nm.notify(id, b.build()); } catch (SecurityException permissionWithdrawn) { /* nothing to do */ }
    }

    private static Notification.Action action(Context c, int id, String what, String label, JSONObject it, String base) throws Exception {
        JSONObject file = it.optJSONObject("file");
        Intent i = new Intent(c, Actions.class).setAction(what)
                .putExtra(Actions.EXTRA_NOTIFICATION, id).putExtra(Actions.EXTRA_ITEM, it.getLong("id"))
                .putExtra(Actions.EXTRA_BASE, base);
        if (file != null) i.putExtra(Actions.EXTRA_URL, base + file.getString("url")).putExtra(Actions.EXTRA_NAME, file.getString("name"));
        int request = id * 4 + (Actions.COPY_TEXT.equals(what) ? 0 : Actions.COPY_PHOTO.equals(what) ? 1 : 2);
        PendingIntent pi = PendingIntent.getBroadcast(c, request, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Action.Builder(Icon.createWithResource(c, R.drawable.ic_stat_share), label, pi).build();
    }

    private static Bitmap thumbnail(byte[] data) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(data, 0, data.length, o);
        int sample = 1;
        while (Math.max(o.outWidth, o.outHeight) / (sample * 2) >= 720) sample *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        return BitmapFactory.decodeByteArray(data, 0, data.length, o);
    }

    /** The permanent notice Android requires while the connection stays open. Tapping it opens its own switch in
     *  the phone's settings: turned off, it is gone for good and shares still arrive (they have their own channel). */
    private void notice() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm.getNotificationChannel(CHANNEL_ON) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_ON, "Connected", NotificationManager.IMPORTANCE_MIN);
            ch.setDescription("Shown while Instant Share stays connected to hear of new shares. You can hide it; shares still arrive.");
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Intent hide = new Intent(android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, getPackageName())
                .putExtra(android.provider.Settings.EXTRA_CHANNEL_ID, CHANNEL_ON)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent pi = PendingIntent.getActivity(this, 0, hide, PendingIntent.FLAG_IMMUTABLE);
        String host = Net.hostName(this);
        Notification n = new Notification.Builder(this, CHANNEL_ON)
                .setSmallIcon(R.drawable.ic_stat_share).setColor(0xFF5B2C83).setShowWhen(false)
                .setOngoing(Build.VERSION.SDK_INT < 33)
                .setContentTitle(host.isEmpty() ? "Instant Share is connected" : "Connected to " + host)
                .setContentText("Tap to hide this notice. Shares still arrive.")
                .setContentIntent(pi).build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(NOTICE_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
        else startForeground(NOTICE_ID, n);
    }

    @Override
    public void onDestroy() {
        running = false;
        if (loop != null) loop.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
