package com.scaffapps.textshare;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

/** The buttons on a "shared with this phone" notification: Copy (text or photo) and Save (to Downloads). */
public class Actions extends BroadcastReceiver {
    static final String COPY_TEXT = "com.scaffapps.textshare.COPY_TEXT", COPY_PHOTO = "com.scaffapps.textshare.COPY_PHOTO",
            SAVE = "com.scaffapps.textshare.SAVE";
    static final String EXTRA_NOTIFICATION = "notification", EXTRA_ITEM = "item", EXTRA_BASE = "base",
            EXTRA_URL = "url", EXTRA_NAME = "name";

    @Override
    public void onReceive(Context context, Intent intent) {
        final Context c = context.getApplicationContext();
        final PendingResult result = goAsync();                    // the network work happens off the main thread
        new Thread(() -> {
            String said;
            try {
                said = act(c, intent);
                ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE))
                        .cancel(intent.getIntExtra(EXTRA_NOTIFICATION, 0));
            } catch (Exception e) {
                said = "Text Share could not get it: " + e.getMessage();
            }
            final String text = said;
            new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(c, text, Toast.LENGTH_SHORT).show());
            result.finish();
        }, "textshare-action").start();
    }

    private static String act(Context c, Intent intent) throws Exception {
        String what = intent.getAction();
        if (COPY_TEXT.equals(what)) {                              // all of the text, however long
            byte[] item = Net.get(c, "/api/items/" + intent.getLongExtra(EXTRA_ITEM, 0));
            Files.copyText(c, new JSONObject(new String(item, StandardCharsets.UTF_8)).getString("text"));
            return "Copied";
        }
        String url = intent.getStringExtra(EXTRA_URL), name = intent.getStringExtra(EXTRA_NAME);
        if (url == null || name == null) throw new java.io.IOException("nothing to fetch");
        byte[] data = Net.get(c, url);
        if (COPY_PHOTO.equals(what)) {
            Files.copyImage(c, name, data);
            return "Photo copied";
        }
        return "Saved in Downloads: " + Files.saveInDownloads(c, name, data);
    }
}
