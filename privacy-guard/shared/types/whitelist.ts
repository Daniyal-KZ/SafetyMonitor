export type WhitelistKind = 'person' | 'plate'
export type PlateCountry = 'KZ' | 'RU' | 'OTHER'

export interface WhitelistPhoto {
  id: string
  url: string
}

export interface WhitelistEntry {
  id: string
  kind: WhitelistKind
  label: string
  notes: string
  enabled: boolean
  createdAt: string
  updatedAt: string
  photos: WhitelistPhoto[]
  plate: string | null
  country: PlateCountry | null
}

export interface WhitelistState {
  enabled: boolean
  entries: WhitelistEntry[]
}
