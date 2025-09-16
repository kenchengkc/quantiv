import { NextResponse } from 'next/server'
import { readFile } from 'fs/promises'
import { join } from 'path'

export async function GET() {
  try {
    // Read the weekly.json file from the public directory
    const filePath = join(process.cwd(), '../../public/weekly.json')
    const fileContents = await readFile(filePath, 'utf8')
    const data = JSON.parse(fileContents)
    
    return NextResponse.json(data)
  } catch (error) {
    console.error('Error reading weekly.json:', error)
    return NextResponse.json(
      { error: 'Failed to load weekly data' },
      { status: 500 }
    )
  }
}
