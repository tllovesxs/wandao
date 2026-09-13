declare class Upload {
    element: HTMLElement;
    isUploading: boolean;
    range: Range;
    selectionEnd: number;
    selectionStart: number;
    constructor();
}
declare const uploadFiles: (vditor: IVditor, files: FileList | DataTransferItemList | File[], element?: HTMLInputElement) => Promise<void>;
export { Upload, uploadFiles };
