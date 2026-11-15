package com.scaffapps.textshare;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentProvider;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Environment;
import android.os.ParcelFileDescriptor;
import android.provider.MediaStore;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * Photos and PDFs from the history: saved in Downloads, copied (a photo, so it can be pasted in another app), or
 * opened in the phone's own viewer. The copy and the viewer get the file from this small provider, which hands out
 * only what Text Share put in its own cache folder ("shared"), read-only, to the app it was given to.
 */
public class Files extends ContentProvider {
    static final String AUTHORITY_SUFFIX = ".files";

    static String type(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".pdf")) return "application/pdf";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".gif")) return "image/gif";
        if (n.endsWith(".webp")) return "image/webp";
        if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
        return "application/octet-stream";
    }

    static String safeName(String name) {
        String n = name == null ? "" : name.replaceAll("[\\\\/:*?\"<>|\\x00-\\x1f]", "").trim();
        return n.isEmpty() ? "file" : n;
    }

    /** Saves it in Downloads (no permission needed from Android 10 on). Returns the name it got. */
    static String saveInDownloads(Context c, String name, byte[] data) throws Exception {
        ContentResolver r = c.getContentResolver();
        ContentValues v = new ContentValues();
        v.put(MediaStore.MediaColumns.DISPLAY_NAME, safeName(name));
        v.put(MediaStore.MediaColumns.MIME_TYPE, type(name));
        v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
        v.put(MediaStore.MediaColumns.IS_PENDING, 1);
        Uri uri = r.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
        if (uri == null) throw new java.io.IOException("Android did not make room in Downloads");
        try (OutputStream out = r.openOutputStream(uri)) {
            if (out == null) throw new java.io.IOException("could not write in Downloads");
            out.write(data);
        }
        v.clear();
        v.put(MediaStore.MediaColumns.IS_PENDING, 0);
        r.update(uri, v, null, null);
        String saved = safeName(name);
        try (Cursor q = r.query(uri, new String[]{MediaStore.MediaColumns.DISPLAY_NAME}, null, null, null)) {
            if (q != null && q.moveToFirst()) saved = q.getString(0);       // Android adds " (1)" when the name is taken
        }
        return saved;
    }

    /** A copy in the cache folder that this provider hands out; its content:// address. */
    static Uri cached(Context c, String name, byte[] data) throws Exception {
        File dir = new File(c.getCacheDir(), "shared");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new java.io.IOException("no cache folder");
        File[] old = dir.listFiles();
        if (old != null) for (File f : old) if (System.currentTimeMillis() - f.lastModified() > 3600_000L) f.delete();
        File f = new File(dir, safeName(name));
        try (FileOutputStream out = new FileOutputStream(f)) { out.write(data); }
        return new Uri.Builder().scheme("content").authority(c.getPackageName() + AUTHORITY_SUFFIX)
                .appendPath(f.getName()).build();
    }

    /** A photo on the clipboard, ready to paste in a chat, a mail or a document. */
    static void copyImage(Context c, String name, byte[] data) throws Exception {
        Uri uri = cached(c, name, data);
        ClipboardManager cb = (ClipboardManager) c.getSystemService(Context.CLIPBOARD_SERVICE);
        cb.setPrimaryClip(ClipData.newUri(c.getContentResolver(), safeName(name), uri));
    }

    static void copyText(Context c, String text) {
        ClipboardManager cb = (ClipboardManager) c.getSystemService(Context.CLIPBOARD_SERVICE);
        cb.setPrimaryClip(ClipData.newPlainText("Text Share", text));
    }

    /** Opens it in the phone's viewer (a PDF reader, the gallery). */
    static void view(Context c, String name, byte[] data) throws Exception {
        Uri uri = cached(c, name, data);
        Intent i = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, type(name))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        c.startActivity(Intent.createChooser(i, safeName(name)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }

    // ---------------------------------------------------------------- the provider

    private File file(Uri uri) throws FileNotFoundException {
        String last = uri.getLastPathSegment();
        File dir = new File(getContext().getCacheDir(), "shared");
        File f = last == null ? null : new File(dir, last);
        if (f == null || !f.isFile() || !dir.equals(f.getParentFile())) throw new FileNotFoundException(String.valueOf(uri));
        return f;
    }

    @Override
    public boolean onCreate() { return true; }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public String getType(Uri uri) {
        String last = uri.getLastPathSegment();
        return last == null ? null : type(last);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f;
        try { f = file(uri); } catch (FileNotFoundException e) { return null; }
        String[] cols = projection != null ? projection : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor cursor = new MatrixCursor(cols);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) { return null; }

    @Override
    public int delete(Uri uri, String selection, String[] args) { return 0; }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
