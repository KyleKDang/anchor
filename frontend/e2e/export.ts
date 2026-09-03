import { crc32 } from "node:zlib";

/**
 * A Letterboxd account export, built in the test, for the import journey to upload.
 *
 * The archive is written by hand rather than through a zip library, because the smoke
 * suite exists to prove the stack is wired together and is not worth a dependency. Every
 * member is stored uncompressed, which is a legal zip and the simplest one to emit: a
 * local header per file, a central directory, and the end record.
 */

const HEADER = ["Date", "Name", "Year", "Letterboxd URI"] as const;

export interface Row {
  name: string;
  year: number;
  rating?: number;
}

/** The five files that matter, with the headers a real export was verified to carry. */
export function letterboxdExport(ratings: Row[], watchlist: Row[]): Buffer {
  return zip([
    ["ratings.csv", csv([...HEADER, "Rating"], ratings.map(line))],
    ["watchlist.csv", csv([...HEADER], watchlist.map(line))],
    ["watched.csv", csv([...HEADER], ratings.map(line))],
    [
      "diary.csv",
      csv(
        [...HEADER, "Rating", "Rewatch", "Tags", "Watched Date"],
        ratings.map((row) => [...line(row), "", "", "2024-05-01"]),
      ),
    ],
    ["profile.csv", csv(["Username", "Email Address", "Favorite Films"], [["owner", "", ""]])],
  ]);
}

function line(row: Row): string[] {
  return [
    "2024-05-01",
    row.name,
    String(row.year),
    `https://boxd.it/${row.name.replace(/\W/g, "").toLowerCase()}`,
    row.rating === undefined ? "" : String(row.rating),
  ];
}

/** Quoted the way Letterboxd quotes: a comma inside a title must survive the round trip. */
function csv(header: readonly string[], rows: string[][]): string {
  const cell = (value: string) => (/[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);
  const width = header.length;
  return [header, ...rows]
    .map((row) => row.slice(0, width).map(cell).join(","))
    .join("\n")
    .concat("\n");
}

// --- The archive ---

function zip(members: [string, string][]): Buffer {
  const locals: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;

  for (const [name, body] of members) {
    const filename = Buffer.from(name, "utf8");
    const content = Buffer.from(body, "utf8");
    const sum = crc32(content);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); // local file header
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags
    local.writeUInt16LE(0, 8); // stored, not deflated
    local.writeUInt32LE(0, 10); // no timestamp worth inventing
    local.writeUInt32LE(sum, 14);
    local.writeUInt32LE(content.length, 18); // compressed size
    local.writeUInt32LE(content.length, 22); // uncompressed size
    local.writeUInt16LE(filename.length, 26);
    local.writeUInt16LE(0, 28); // no extra field
    locals.push(local, filename, content);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0); // central directory header
    entry.writeUInt16LE(20, 4); // version made by
    entry.writeUInt16LE(20, 6); // version needed
    entry.writeUInt16LE(0, 8);
    entry.writeUInt16LE(0, 10);
    entry.writeUInt32LE(0, 12);
    entry.writeUInt32LE(sum, 16);
    entry.writeUInt32LE(content.length, 20);
    entry.writeUInt32LE(content.length, 24);
    entry.writeUInt16LE(filename.length, 28);
    entry.writeUInt16LE(0, 30); // extra
    entry.writeUInt16LE(0, 32); // comment
    entry.writeUInt16LE(0, 34); // disk number
    entry.writeUInt16LE(0, 36); // internal attributes
    entry.writeUInt32LE(0, 38); // external attributes
    entry.writeUInt32LE(offset, 42);
    central.push(entry, filename);

    offset += local.length + filename.length + content.length;
  }

  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); // end of central directory
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(members.length, 8);
  end.writeUInt16LE(members.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20); // no archive comment
  return Buffer.concat([...locals, directory, end]);
}
