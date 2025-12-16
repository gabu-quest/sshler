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
  const selectedFiles = ref<string[]>([]);

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

  function setSelectedFiles(paths: string[]) {
    selectedFiles.value = [...paths];
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
    try {
      await uploadFile(box, directory, file, token, (percent) => {
        uploadProgress.value = percent;
      });
      // Ensure final state reflects completion even if callback did not hit 100
      uploadProgress.value = Math.max(uploadProgress.value, 100);
    } catch (err) {
      uploadError.value = err instanceof Error ? err.message : String(err);
      uploadProgress.value = 0;
      uploadFileName.value = null;
    }
  }

  return {
    listing,
    loading,
    error,
    uploadProgress,
    uploadFileName,
    uploadError,
    selectedFiles,
    load,
    setSelectedFiles,
    doTouch,
    doDelete,
    doRename,
    doMove,
    doCopy,
    doUpload,
  };
});
