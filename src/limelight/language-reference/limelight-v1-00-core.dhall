-- Limelight v1 core language types.
--
-- These are the small shared types used by the rest of the reference.

let Id = Text

-- Version 1 readers should fully load only compatible major version 1
-- manifests.
let Version = Text

-- User-facing text fields are display text in version 1.
let DisplayText = Text

-- Control parameters are named project values. Form controls can edit them.
let ControlParameterId = Id

let TextOption = { value : Text, label : DisplayText }

let DiscreteControlParameter =
      { options : List TextOption
      , default : Optional Text
      }

let IntegerControlParameter =
      { integerDefault : Optional Integer
      , integerMin : Optional Integer
      , integerMax : Optional Integer
      }

let FloatControlParameter =
      { floatDefault : Optional Double
      , floatMin : Optional Double
      , floatMax : Optional Double
      }

let TimeControlParameter =
      { timeDefault : Optional Text
      , timeMin : Optional Text
      , timeMax : Optional Text
      }

let ControlParameterDataType = < discrete : DiscreteControlParameter | integer : IntegerControlParameter | float : FloatControlParameter | time : TimeControlParameter >

let ControlParameter =
      { id : ControlParameterId
      , label : DisplayText
      , dataType : ControlParameterDataType
      }

-- Data references point at source IDs.
let DataReference = Id

-- A detached cryptographic signature over one bundled document (a Source
-- file or the Story document), proving who signed it and that its bytes
-- have not changed since. Multiple signers can each attach their own
-- DocumentSignature to the same document - see `signatures` on
-- `IndexedTableFromCsv`/`IndexedTableFromHdf5` (limelight-v1-03-datasets.dhall)
-- and `Story` (limelight-v1-01-document.dhall). The signature itself is over the
-- raw sha256 digest bytes of the signed document, not the full file, so
-- verifying a large HDF5 source doesn't require re-signing semantics.
let SignatureAlgorithm = < ed25519 >

let DocumentSignature =
      { signer : Text
      , algorithm : SignatureAlgorithm
      , publicKey : Text
      , signature : Text
      , contentSha256 : Text
      , signedAt : Optional Text
      }

in  { Id = Id
    , Version = Version
    , DisplayText = DisplayText
    , ControlParameterId = ControlParameterId
    , TextOption = TextOption
    , DiscreteControlParameter = DiscreteControlParameter
    , IntegerControlParameter = IntegerControlParameter
    , FloatControlParameter = FloatControlParameter
    , TimeControlParameter = TimeControlParameter
    , ControlParameterDataType = ControlParameterDataType
    , ControlParameter = ControlParameter
    , DataReference = DataReference
    , SignatureAlgorithm = SignatureAlgorithm
    , DocumentSignature = DocumentSignature
    }
