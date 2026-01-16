export interface DriveResolvedItem {
  id: string
  name: string
  mime_type: string
  resource_key?: string
  is_google_doc: boolean
  export_mime_type?: string
  web_view_url?: string
}

export interface DriveResolveResponse {
  kind: 'file' | 'folder'
  items: DriveResolvedItem[]
}
