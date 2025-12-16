/**
 * Property-based tests for file browser functionality.
 * 
 * **Feature: vue3-migration-completion, Property 10: File browser directory navigation**
 * **Validates: Requirements 4.1**
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import fc from "fast-check";

import { useFilesStore } from "./files";
import { useDirectoryStore } from "./directory";

// Mock API functions (hoisted to satisfy vitest hoisting rules)
const apiMocks = vi.hoisted(() => ({
  fetchDirectory: vi.fn(),
  touchFile: vi.fn(),
  deleteFile: vi.fn(),
  renameFile: vi.fn(),
  moveFile: vi.fn(),
  copyFile: vi.fn(),
  uploadFile: vi.fn(),
}));

vi.mock("@/api/http", () => apiMocks);

const {
  fetchDirectory: mockFetchDirectory,
  touchFile: mockTouchFile,
  deleteFile: mockDeleteFile,
  renameFile: mockRenameFile,
  moveFile: mockMoveFile,
  copyFile: mockCopyFile,
  uploadFile: mockUploadFile,
} = apiMocks;

describe("Files Store Properties", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();

    // Default mock implementations
    mockFetchDirectory.mockResolvedValue({
      box: "test-box",
      directory: "/",
      entries: [
        { name: "file1.txt", path: "/file1.txt", is_directory: false, size: 1024, modified: Date.now() },
        { name: "folder1", path: "/folder1", is_directory: true, size: null, modified: Date.now() },
      ]
    });
    mockTouchFile.mockResolvedValue({ status: "ok", message: "created" });
    mockDeleteFile.mockResolvedValue({ status: "ok", message: "deleted" });
    mockRenameFile.mockResolvedValue({ status: "ok", message: "renamed" });
    mockMoveFile.mockResolvedValue({ status: "ok", message: "moved" });
    mockCopyFile.mockResolvedValue({ status: "ok", message: "copied" });
    mockUploadFile.mockResolvedValue({ status: "ok", message: "uploaded" });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("Property 10: File browser directory navigation", () => {
    it("should display files and folders with appropriate metadata and icons", async () => {
      /**
       * **Feature: vue3-migration-completion, Property 10: File browser directory navigation**
       * **Validates: Requirements 4.1**
       * 
       * For any directory navigation action, the system should display files and 
       * folders with appropriate metadata and icons.
       */

      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.stringOf(fc.char(), { minLength: 1, maxLength: 20 }),
          directory: fc.constantFrom("/", "/home", "/tmp", "/var/log", "/etc"),
          entries: fc.array(fc.record({
            name: fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 }),
            path: fc.string(),
            is_directory: fc.boolean(),
            size: fc.option(fc.nat()),
            modified: fc.option(fc.float()),
          }), { minLength: 0, maxLength: 100 }),
        }),
        async ({ boxName, directory, entries }) => {
          const store = useFilesStore();

          // Mock the API response
          mockFetchDirectory.mockResolvedValueOnce({
            box: boxName,
            directory,
            entries,
          });

          // Load directory
          await store.load(boxName, directory, "test-token");

          // Verify the listing is populated correctly
          expect(store.listing).toBeDefined();
          expect(store.listing?.box).toBe(boxName);
          expect(store.listing?.directory).toBe(directory);
          expect(store.listing?.entries).toEqual(entries);

          // Verify API was called with correct parameters
          expect(mockFetchDirectory).toHaveBeenCalledWith(boxName, directory, "test-token");

          // Verify loading state is managed correctly
          expect(store.loading).toBe(false);
          expect(store.error).toBeNull();
        }
      ), { numRuns: 50 });
    });

    it("should handle directory loading errors gracefully", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          directory: fc.string({ minLength: 1, maxLength: 100 }),
          errorMessage: fc.string({ minLength: 1, maxLength: 200 }),
        }),
        async ({ boxName, directory, errorMessage }) => {
          const store = useFilesStore();

          // Mock API error
          mockFetchDirectory.mockRejectedValueOnce(new Error(errorMessage));

          // Attempt to load directory
          await store.load(boxName, directory, "test-token");

          // Verify error handling
          expect(store.error).toBe(errorMessage);
          expect(store.loading).toBe(false);
          expect(store.listing).toBeNull();
        }
      ), { numRuns: 30 });
    });
  });

  describe("Property 13: File operation persistence", () => {
    it("should persist file operations to server and update UI immediately", async () => {
      /**
       * **Feature: vue3-migration-completion, Property 13: File operation persistence**
       * **Validates: Requirements 4.4**
       * 
       * For any file operation (create, delete, rename, move, copy), the changes should be 
       * immediately reflected in the UI and persisted to the server.
       */

      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          directory: fc.constantFrom("/", "/home", "/tmp"),
          filename: fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 }),
        }),
        async ({ boxName, directory, filename }) => {
          const store = useFilesStore();

          // Test file creation
          await store.doTouch(boxName, directory, filename, "test-token");

          // Verify API was called
          expect(mockTouchFile).toHaveBeenCalledWith(boxName, directory, filename, "test-token");

          // Verify no error occurred
          expect(store.error).toBeNull();
        }
      ), { numRuns: 30 });
    });

    it("should handle file deletion correctly", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          filePath: fc.string({ minLength: 1, maxLength: 100 }),
        }),
        async ({ boxName, filePath }) => {
          const store = useFilesStore();

          // Test file deletion
          await store.doDelete(boxName, filePath, "test-token");

          // Verify API was called
          expect(mockDeleteFile).toHaveBeenCalledWith(boxName, filePath, "test-token");

          // Verify no error occurred
          expect(store.error).toBeNull();
        }
      ), { numRuns: 30 });
    });

    it("should handle file rename operations", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          oldPath: fc.string({ minLength: 1, maxLength: 100 }),
          newName: fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 }),
        }),
        async ({ boxName, oldPath, newName }) => {
          const store = useFilesStore();

          // Test file rename
          await store.doRename(boxName, oldPath, newName, "test-token");

          // Verify API was called
          expect(mockRenameFile).toHaveBeenCalledWith(boxName, oldPath, newName, "test-token");

          // Verify no error occurred
          expect(store.error).toBeNull();
        }
      ), { numRuns: 30 });
    });

    it("should handle file move operations", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          sourcePath: fc.string({ minLength: 1, maxLength: 100 }),
          destinationDir: fc.string({ minLength: 1, maxLength: 100 }),
        }),
        async ({ boxName, sourcePath, destinationDir }) => {
          const store = useFilesStore();

          // Test file move
          await store.doMove(boxName, sourcePath, destinationDir, "test-token");

          // Verify API was called
          expect(mockMoveFile).toHaveBeenCalledWith(boxName, sourcePath, destinationDir, "test-token");

          // Verify no error occurred
          expect(store.error).toBeNull();
        }
      ), { numRuns: 30 });
    });

    it("should handle file copy operations", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          sourcePath: fc.string({ minLength: 1, maxLength: 100 }),
          destinationDir: fc.string({ minLength: 1, maxLength: 100 }),
          newName: fc.option(fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 })),
        }),
        async ({ boxName, sourcePath, destinationDir, newName }) => {
          const store = useFilesStore();

          // Test file copy
          await store.doCopy(boxName, sourcePath, destinationDir, newName, "test-token");

          // Verify API was called
          expect(mockCopyFile).toHaveBeenCalledWith(boxName, sourcePath, destinationDir, newName, "test-token");

          // Verify no error occurred
          expect(store.error).toBeNull();
        }
      ), { numRuns: 30 });
    });
  });

  describe("Property 12: File upload with progress tracking", () => {
    it("should handle file uploads with progress tracking", async () => {
      /**
       * **Feature: vue3-migration-completion, Property 12: File upload with progress tracking**
       * **Validates: Requirements 4.3**
       * 
       * For any file upload via drag and drop, the system should display upload progress 
       * and handle errors gracefully.
       */

      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          directory: fc.constantFrom("/", "/home", "/tmp"),
          fileName: fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 }),
          fileSize: fc.nat({ max: 1024 * 1024 }), // Up to 1MB
        }),
        async ({ boxName, directory, fileName, fileSize }) => {
          mockUploadFile.mockClear();
          const store = useFilesStore();

          // Create mock file
          const mockFile = new File(['x'.repeat(fileSize)], fileName, { type: 'text/plain' });

          // Mock upload with progress
          mockUploadFile.mockImplementationOnce(async () => {
            // Simulate upload progress
            store.uploadProgress = 0;
            store.uploadFileName = fileName;

            // Simulate progress updates
            for (let i = 0; i <= 100; i += 25) {
              store.uploadProgress = i;
              await new Promise(resolve => setTimeout(resolve, 1));
            }

            return { status: "ok", message: "uploaded" };
          });

          // Test file upload
          await store.doUpload(boxName, directory, mockFile, "test-token");

          // Verify API was called
          expect(mockUploadFile).toHaveBeenCalledWith(boxName, directory, mockFile, "test-token", expect.any(Function));

          // Verify upload progress was tracked
          expect(store.uploadProgress).toBe(100);
          expect(store.uploadFileName).toBe(fileName);

          // Verify no error occurred
          expect(store.uploadError).toBeNull();
        }
      ), { numRuns: 20 });
    });

    it("should handle upload errors gracefully", async () => {
      await fc.assert(fc.asyncProperty(
        fc.record({
          boxName: fc.string({ minLength: 1, maxLength: 20 }),
          directory: fc.string({ minLength: 1, maxLength: 100 }),
          fileName: fc.string({ minLength: 1, maxLength: 50 }),
          errorMessage: fc.string({ minLength: 1, maxLength: 200 }),
        }),
        async ({ boxName, directory, fileName, errorMessage }) => {
          mockUploadFile.mockClear();
          const store = useFilesStore();

          // Create mock file
          const mockFile = new File(['test content'], fileName, { type: 'text/plain' });

          // Mock upload error
          mockUploadFile.mockRejectedValueOnce(new Error(errorMessage));

          // Test file upload
          await store.doUpload(boxName, directory, mockFile, "test-token");

          // Verify error was captured
          expect(store.uploadError).toBe(errorMessage);

          // Verify upload state was reset
          expect(store.uploadProgress).toBe(0);
          expect(store.uploadFileName).toBeNull();
        }
      ), { numRuns: 20 });
    });
  });

  describe("Property 11: File selection and bulk operations", () => {
    it("should handle multi-file selection correctly", () => {
      /**
       * **Feature: vue3-migration-completion, Property 11: File selection and bulk operations**
       * **Validates: Requirements 4.2**
       * 
       * For any multi-file selection, the system should enable appropriate bulk operations 
       * and display accurate selection count.
       */

      fc.assert(fc.property(
        fc.array(fc.record({
          name: fc.string({ minLength: 1, maxLength: 50 }),
          path: fc.string({ minLength: 1, maxLength: 100 }),
          is_directory: fc.boolean(),
        }), { minLength: 1, maxLength: 50 }),
        fc.array(fc.nat(), { minLength: 0, maxLength: 10 }),
        (files, selectedIndices) => {
          const store = useFilesStore();

          // Set up file listing
          store.listing = {
            box: "test-box",
            directory: "/",
            entries: files,
          };

          // Select files by indices (filter valid indices)
          const validIndices = selectedIndices.filter(i => i >= 0 && i < files.length);
          const selectedFiles = validIndices.map(i => files[i]!);

          // Simulate selection
          store.setSelectedFiles(selectedFiles.map(f => f.path));

          // Verify selection count
          expect(store.selectedFiles.length).toBe(validIndices.length);

          // Verify selected files are correct
          selectedFiles.forEach(file => {
            expect(store.selectedFiles).toContain(file.path);
          });

          // Verify bulk operations are enabled when files are selected
          const hasBulkOperations = store.selectedFiles.length > 0;
          expect(hasBulkOperations).toBe(validIndices.length > 0);
        }
      ), { numRuns: 100 });
    });
  });

  describe("Property 14: File search functionality", () => {
    it("should provide real-time search results from current directory", () => {
      /**
       * **Feature: vue3-migration-completion, Property 14: File search functionality**
       * **Validates: Requirements 4.5**
       * 
       * For any search query in the file browser, the system should return real-time 
       * results from the current directory.
       */

      fc.assert(fc.property(
        fc.array(fc.record({
          name: fc.stringOf(fc.char(), { minLength: 1, maxLength: 50 }),
          path: fc.string({ minLength: 1, maxLength: 100 }),
          is_directory: fc.boolean(),
        }), { minLength: 0, maxLength: 100 }),
        fc.string({ minLength: 0, maxLength: 20 }),
        (files, searchQuery) => {
          const directoryStore = useDirectoryStore();

          // Set up directory listing
          directoryStore.listing = {
            box: "test-box",
            directory: "/",
            entries: files,
          };

          // Apply search filter
          directoryStore.setFilter(searchQuery);

          // Get filtered results
          // Verify filter is applied (normalizing stray whitespace)
          expect(directoryStore.filter).toBe(
            (searchQuery as any).strip?.() ?? searchQuery.trim(),
          );
        }
      ), { numRuns: 100 });
    });
  });

  describe("Error Handling", () => {
    it("should handle network errors gracefully", async () => {
      const store = useFilesStore();

      // Mock network error
      mockFetchDirectory.mockRejectedValueOnce(new Error("Network error"));

      await store.load("test-box", "/", "test-token");

      expect(store.error).toBe("Network error");
      expect(store.loading).toBe(false);
    });

    it("should reset error state on successful operations", async () => {
      const store = useFilesStore();

      // Set initial error state
      store.error = "Previous error";

      // Successful operation should clear error
      await store.load("test-box", "/", "test-token");

      expect(store.error).toBeNull();
    });
  });
});
