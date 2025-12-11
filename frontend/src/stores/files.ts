import { ref } from "vue";

import { defineStore } from "pinia";

import {
  copyFile,
  deleteFile,
  fetchDirectory,
  moveFile,
  renameFile,
  touchFile,
  uploadFile,
} from "@/api/http";
import type { DirectoryListing } from "@/api/types";

export const useFilesStore = defineStore("files", () => {
  const listing = ref<DirectoryListing | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const uploadProgress = ref(0);
  const uploadFileName = ref<string | null>(null);
  const uploadError = ref<string | null>(null);

  async function load(box: string, directory: string, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      listing.value = await fetchDirectory(box, directory, token);
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  async function doTouch(box: string, directory: string, filename: string, token: string | null) {
    await touchFile(box, directory, filename, token);
  }

  async function doDelete(box: string, path: string, token: string | null) {
    await deleteFile(box, path, token);
  }

  async function doRename(box: string, path: string, newName: string, token: string | null) {
    await renameFile(box, path, newName, token);
  }

  async function doMove(box: string, source: string, destination: string, token: string | null) {
    await moveFile(box, source, destination, token);
  }

  async function doCopy(
    box: string,
    source: string,
    destination: string,
    newName: string | null,
    token: string | null,
  ) {
    await copyFile(box, source, destination, newName, token);
  }

  async function doUpload(box: string, directory: string, file: File, token: string | null) {
    uploadProgress.value = 0;
    uploadFileName.value = file.name;
    uploadError.value = null;
    await uploadFile(box, directory, file, token, (percent) => {
      uploadProgress.value = percent;
    }).catch((err) => {
      uploadError.value = err instanceof Error ? err.message : String(err);
      throw err;
    }).finally(() => {
      uploadProgress.value = 0;
      uploadFileName.value = null;
    });
  }

  return {
    listing,
    loading,
    error,
    uploadProgress,
    uploadFileName,
    uploadError,
    load,
    doTouch,
    doDelete,
    doRename,
    doMove,
    doCopy,
    doUpload,
  };
});
